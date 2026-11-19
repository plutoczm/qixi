"""Explicit, source-bound POI discovery and nearby hotel distance evidence.

No provider access on page load; the user clicks each network step.  A map POI
is not a priced hotel; price joins require unique supplier identity AND city
provenance. Raw map objects, coordinates, secrets, and opaque provider IDs never
leave short-lived memory or get persisted to SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import threading
import time
from uuid import uuid4

from .contracts import ApiError
from .providers.data.rollinggo import ConfigurationError
from .providers.data.travel_clients import (
    MapProbe, ProbeError, distance_meters, load_key,
)
from .travel_rules import address_matches, city_name, normalized
from .storage import SessionError
from .review_evidence import ReviewCatalog, load_local_review_catalog
from .city_observation import members_snapshot
from .nearby_identity import IdentityError, IdentityState, identity_action

_PROVIDER_LABEL = {"amap": "高德地图", "tencent": "腾讯地图"}
_CHINA = {"中国", "中国大陆", "中华人民共和国", "china", "mainland china", "prc"}
_EXCLUDE = ("停车场", "餐厅", "咖啡", "酒吧", "大堂", "健身房", "写字楼", "充电站", "入口", "出口")
_LIMIT = 10
_CONTEXT_SECONDS = 120
_MAX_CONTEXTS = 64


def observed_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_label(value: object, limit: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _map_rating(value: object) -> float | None:
    """Parse official Amap POI business.rating; never infer a guest score.

    No review count or historical stays are available from this map field.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        score = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not score.is_finite() or not Decimal("0") < score <= Decimal("5"):
        return None
    return float(score)


def _valid_region(country: str | None) -> bool:
    return country is None or country.casefold() in _CHINA


def _explicitly_outside_mainland(meta: dict) -> bool:
    """Reject source-confirmed Hong Kong, Macau and Taiwan POIs.

    Missing/invalid administrative evidence remains unknown, not a claim
    that the resulting POI is definitely located in mainland China.
    """
    code = meta.get("adcode")
    if isinstance(code, (str, int)):
        digits = str(code).strip()
        if len(digits) == 6 and digits.isdecimal():
            if digits[:2] in {"71", "81", "82"}:
                return True
    for admin_field in ("city", "province"):
        name = meta.get(admin_field)
        if isinstance(name, str) and name.strip().casefold().startswith(
            ("香港", "澳门", "澳門", "台湾", "臺灣", "hong kong", "macau", "macao", "taiwan")
        ):
            return True
    return False


def _foreign_message() -> str:
    return "当前高德/腾讯地图流程只按中国大陆已接入能力提供；境外目的地未核验覆盖，本次未请求地图。"


class NearbyError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class NearbyContext:
    provider: str
    city: str
    client: object
    anchors: dict[str, object]
    hotels: dict[str, object]
    expires: float
    selected: str | None = None
    explored: bool = False
    route_attempts: int = 0
    source_request_id: str | None = None
    source_query: dict | None = None
    source_members: tuple = ()
    source_expires: float = 0
    hotel_rows: dict = field(default_factory=dict)
    identity: IdentityState = field(default_factory=IdentityState)
    lock: threading.Lock = field(default_factory=threading.Lock)


class NearbyPlaceService:
    """Single-source point tokens, bounded HTTP, explicit user selection."""

    data_mode = "live"

    def __init__(self, factories=None, *, clock=time.monotonic, data_mode="live",
                 review_catalog: ReviewCatalog | None = None, supplier_source: str | None = None,
                 city_observations=None):
        if (data_mode not in ("live", "test")
            or (factories is not None and data_mode != "test")
            or (review_catalog is not None and data_mode != "test")):
            raise ValueError("TEST_FACTORIES_NOT_ALLOWED")
        self.review_catalog = review_catalog
        self.supplier_source = supplier_source
        self.factories = factories or {
            key: (lambda provider=key: MapProbe(provider, load_key(provider)))
            for key in _PROVIDER_LABEL
        }
        self.clock = clock
        self.data_mode = data_mode
        self.contexts: dict[str, NearbyContext] = {}
        self.lock = threading.Lock()
        self.capacity = threading.BoundedSemaphore(2)
        self.city_observations = city_observations

    @staticmethod
    def error(code: str) -> ApiError:
        messages = {
            "REGION_NOT_SUPPORTED": _foreign_message(),
            "INVALID_ARGUMENTS": "请填写明确的城市和目的地名称，避免过长或不完整的地点。",
            "MISSING_CREDENTIAL": "当前地图来源尚未配置，未发送请求。",
            "INVALID_CREDENTIAL": "当前地图来源配置无效。",
            "AUTHENTICATION_FAILED": "地图来源认证失败，请检查本机配置。",
            "ACCESS_DENIED": "地图来源尚未授权此能力。",
            "SOURCE_DAILY_LIMIT": "地图来源当日请求额度已用尽，本次没有重试。",
            "RATE_LIMITED": "地图请求过于频繁，本次没有重试。",
            "BUSY": "当前附近酒店核对繁忙，请稍后手动操作。",
            "REQUEST_LIMIT": "本次地点核对已达到地图请求上限，没有继续调用。",
            "CONTEXT_NOT_FOUND": "地点上下文不存在或已重启，请重新查地点。",
            "CONTEXT_EXPIRED": "地图点位已过期，请重新查地点并选择。",
            "POINT_NOT_IN_CONTEXT": "所选点位不属于本次地图查询，请重新选择。",
            "CONTEXT_NOT_SELECTED": "请先手动确认一个目的地。",
            "CONTEXT_LOCKED": "本轮已确定一个目的地；请重新搜索以切换地点。",
            "SOURCE_SESSION_MISSING": "原酒店报价查询已过期，不能关联房价；请重新查酒店。",
            "IDENTITY_CONTEXT_MISMATCH": "人工关联只限本轮原报价与地图上下文，不能替换查询。",
            "IDENTITY_SNAPSHOT_CHANGED": "本轮来源成员或身份已变化，人工关联已失效；请重新查询后核对。",
            "IDENTITY_NOT_ELIGIBLE": "仅允许补本轮已观察到的城市缺失；名称、地址、地区与双向唯一门禁仍须满足。",
            "IDENTITY_PAIR_OCCUPIED": "本轮同店对已有关联，请先撤销原确认再重新核对。",
            "IDENTITY_REVISION_CONFLICT": "本地关联版本或操作顺序已变化，请同步当前状态后操作。",
            "IDENTITY_OPERATION_CONFLICT": "原确认操作已变化，本次没有覆盖或撤销新的关联。",
            "IDENTITY_OPERATION_LIMIT": "本轮本地操作已达上限，请重新选择地点；没有自动查源。",
            "TIMEOUT": "地图请求超时，本次未取得新的位置或路线证据；没有自动重试，请在点位有效期内手动操作。",
            "ROUTE_UNAVAILABLE": "未获取本次步行路线，只能使用地图直线距离参考。",
            "UNAVAILABLE": "地图来源暂未返回可核实的数据；本次没有自动重试。",
        }
        return ApiError(code=code, message=messages.get(code, messages["UNAVAILABLE"]))

    def _remove_stale(self) -> None:
        expired = [token for token, row in self.contexts.items() if row.expires <= self.clock()]
        for token in expired:
            self.contexts.pop(token, None)
        while len(self.contexts) >= _MAX_CONTEXTS:
            self.contexts.pop(next(iter(self.contexts)))

    def _context(self, token: str) -> NearbyContext:
        with self.lock:
            row = self.contexts.get(token)
            if row is None:
                raise NearbyError("CONTEXT_NOT_FOUND")
            if row.expires <= self.clock():
                self.contexts.pop(token, None)
                raise NearbyError("CONTEXT_EXPIRED")
            return row

    def _begin_map_step(self, context: NearbyContext) -> None:
        """A user action gets its own transport deadline, bounded by old POI expiry.

        Reuse the same client and points: counters, pacing, cancellation and
        source identity must survive the user's time spent confirming a POI.
        Other travel workflows retain their original shared transport deadline.
        """
        remaining = context.expires - self.clock()
        if remaining <= 0:
            raise NearbyError("CONTEXT_EXPIRED")
        http = getattr(context.client, "http", None)
        if http is not None:
            http.deadline = time.monotonic() + min(
                60, getattr(http, "total_timeout", 60), remaining,
            )

    def resolve(self, request):
        """One explicit map POI text search. Never auto-picks first candidate."""
        base = {
            "provider": request.provider,
            "queried_at": observed_at(),
            "data_mode": self.data_mode,
            "context_id": None,
            "status": "unavailable",
            "options": [],
            "expires_in_seconds": 0,
            "coverage_note": "本次地图检索最多读取10条候选，按同城证据及名称相关性展示最多5条；仍需人工核对分店，不代表城市完整地点库。",
        }
        if not _valid_region(request.country_region):
            return {**base, "error": self.error("REGION_NOT_SUPPORTED")}
        if (
            len(request.city.encode("utf-8")) > 96
            or len(request.keyword.encode("utf-8")) > 96
            or any(c in request.city for c in "(),\r\n")
        ):
            return {**base, "error": self.error("INVALID_ARGUMENTS")}
        if not self.capacity.acquire(blocking=False):
            return {**base, "error": self.error("BUSY")}
        try:
            client = self.factories[request.provider]()
            # One bounded source request. Search more candidates on the SAME page
            # to avoid first-five bias; do not silently request a second page.
            points = client.search(request.keyword, request.city, page=1, page_size=10)
            ranked = []
            seen = set()
            term = normalized(request.keyword)
            for index, point in enumerate(points[:10]):
                if point.poi_id in seen:
                    continue
                seen.add(point.poi_id)
                info = client.metadata.get(point.poi_id, {})
                if _explicitly_outside_mainland(info):
                    continue
                name = _safe_label(point.title, 120)
                city = _safe_label(info.get("city"), 60)
                if not name or (city and city_name(city) != city_name(request.city)):
                    # Provider-side city boundary alone is not identity proof.
                    continue
                target = normalized(name)
                relation = (
                    0 if target == term else
                    1 if target.startswith(term) else
                    2 if term in target else 3
                )
                # Same-city evidence and name relevance are ordering aids,
                # NOT confidence scores or permission to auto-select a POI.
                ranked.append((0 if city else 1, relation, index, point, info, name, city))
            ranked.sort(key=lambda item: item[:3])
            options = []
            refs = {}
            for _, _, _, point, info, name, city in ranked[:5]:
                option_id = uuid4().hex
                refs[option_id] = point
                options.append({
                    "id": option_id,
                    "name": name,
                    "address": _safe_label(info.get("address"), 240),
                    "city": city,
                    "category": _safe_label(info.get("type") or info.get("category"), 100),
                })
            with self.lock:
                self._remove_stale()
                context_id = uuid4().hex
                self.contexts[context_id] = NearbyContext(
                    request.provider, request.city, client, refs, {},
                    self.clock() + _CONTEXT_SECONDS,
                )
            return {
                **base, "context_id": context_id,
                "status": "partial" if not options else "complete",
                "options": options, "expires_in_seconds": _CONTEXT_SECONDS,
                "error": None,
            }
        except (ProbeError, ConfigurationError, OSError, KeyError) as failure:
            return {**base, "error": self.error(_probe_code(failure))}
        finally:
            self.capacity.release()

    def explore(self, request, sessions, *, recommendation_requests=()):
        """One map nearby search; optionally join existing price session by strict identity."""
        base = {
            "context_id": request.context_id,
            "anchor_id": request.anchor_id,
            "provider": "amap",
            "queried_at": observed_at(),
            "data_mode": self.data_mode,
            "status": "unavailable",
            "hotels": [],
            "coverage_note": "地图2公里范围内最多10个酒店POI，未对全城或库存进行枚举。",
            "ranking_status": "not_comparable",
            "ranking_reason": "地图不提供可核实的住客评价人数及实际入住人数，不能综合排名；高德POI评分如有仅为该地图的地点评分，不是住客评分。",
            "error": None,
        }
        try:
            context = self._context(request.context_id)
            base["provider"] = context.provider
            base["coverage_note"] = (
                "高德按点位搜索半径约2公里，最多本轮10个酒店POI；来源返回不是全城完整库存。"
                if context.provider == "amap"
                else "腾讯按点位搜索半径约1公里，最多本轮10个酒店POI；来源返回不是全城完整库存。"
            )
            if request.anchor_id not in context.anchors:
                raise NearbyError("POINT_NOT_IN_CONTEXT")
            if not context.lock.acquire(blocking=False):
                raise NearbyError("BUSY")
        except NearbyError as failure:
            return {**base, "error": self.error(failure.code)}
        try:
            if context.selected is not None and context.selected != request.anchor_id:
                raise NearbyError("CONTEXT_LOCKED")
            if context.explored:
                # No invisible new network call or replay of prior sources.
                raise NearbyError("CONTEXT_LOCKED")
            # Validate user-owned source session before spending the map request.
            source_query, source_hotels = None, None
            if request.request_id:
                try:
                    source_query, source_hotels = sessions.list_hotels(request.request_id)
                except (SessionError, AttributeError):
                    raise NearbyError("SOURCE_SESSION_MISSING") from None
            if not self.capacity.acquire(blocking=False):
                raise NearbyError("BUSY")
            try:
                self._begin_map_step(context)
                selected = context.anchors[request.anchor_id]
                nearby = context.client.around(selected, "酒店", limit=_LIMIT)
            finally:
                self.capacity.release()
            if context.expires <= self.clock():
                raise NearbyError("CONTEXT_EXPIRED")
            context.selected = request.anchor_id
            context.explored = True
            found = []
            seen = set()
            for point in nearby:
                if point.poi_id in seen:
                    continue
                seen.add(point.poi_id)
                meta = context.client.metadata.get(point.poi_id, {})
                if _explicitly_outside_mainland(meta):
                    continue
                name = _safe_label(point.title, 120)
                address = _safe_label(meta.get("address"), 240)
                city = _safe_label(meta.get("city"), 60)
                if not name or not address or not city or any(word in name for word in _EXCLUDE):
                    continue
                if city_name(city) != city_name(context.city):
                    continue
                # Source nearby radius is not assumed accurate; recheck same-source coordinates.
                metres = round(distance_meters(selected.lng, selected.lat, point.lng, point.lat))
                source_radius = 2000 if context.provider == "amap" else 1000
                if metres < 0 or metres > source_radius:
                    continue
                category = _safe_label(meta.get("type") or meta.get("category"), 100) or ""
                if (
                    any(kind in category for kind in ("餐饮服务", "购物服务", "公司企业", "交通设施"))
                    and not any(kind in category for kind in ("住宿服务", "酒店", "民宿", "旅馆", "客栈"))
                ):
                    continue
                map_rating = (
                    _map_rating(meta.get("map_business_rating"))
                    if context.provider == "amap" else None
                )
                option_id = uuid4().hex
                context.hotels[option_id] = point
                found.append({
                    "id": option_id, "name": name, "address": address,
                    "city": city, "straight_line_meters": metres,
                    "source_hotel_id": None, "source_match": "unverified",
                    "walking_distance_meters": None, "walking_duration_seconds": None,
                    "rating_status": "map_poi" if map_rating is not None else "unavailable",
                    "map_rating": map_rating,
                    "review_count": None, "stayed_count": None,
                })
            # One existing supplier session only. No additional room searches here.
            if source_query is not None and source_hotels is not None:
                _join_supplier_matches(found, source_hotels, source_query, context.city)
                catalog = (self.review_catalog if self.data_mode == "test"
                           else load_local_review_catalog())
                if (catalog is not None and self.supplier_source is not None
                    and source_query.get("_supplier_source") == self.supplier_source):
                    count = catalog.apply(found, source_hotels, context.city,
                                          supplier_source=self.supplier_source)
                    if count:
                        base["ranking_reason"] = (
                            f"本轮有 {count} 家酒店具备本机许可登记的住客评分和评价人数；"
                            "仍需同轮房价及路线核验才可参与四因素排序。"
                            "数据授权需维护人核对真实合同。"
                        )
                context.source_request_id = request.request_id
                context.source_query = source_query.copy()
                context.source_members = members_snapshot(source_hotels)
                # Convert SQL epoch expiry to map monotonic remaining time ONCE.
                if hasattr(sessions, "ensure_live") and hasattr(sessions, "clock"):
                    context.source_expires = min(
                        context.expires, self.clock() + max(
                            0, sessions.ensure_live(request.request_id) - sessions.clock(),
                        ),
                    )
            context.hotel_rows = {row["id"]: row.copy() for row in found}
            found.sort(key=lambda hotel: (hotel["straight_line_meters"], hotel["name"], hotel["id"]))
            response = {
                **base, "status": "complete" if found else "partial",
                "hotels": found, "error": None,
            }
            if recommendation_requests:
                links = []
                for request_id in recommendation_requests:
                    try:
                        query, hotels = sessions.list_hotels(request_id)
                    except SessionError:
                        continue
                    if normalized(query.get("poi_name")) != normalized(selected.title):
                        continue
                    rows = [dict(row, source_hotel_id=None, source_match="unverified") for row in found]
                    _join_supplier_matches(rows, hotels, query, context.city)
                    links.extend(
                        {"request_id": request_id, "hotel_id": row["source_hotel_id"],
                         "hotel_option_id": row["id"]}
                        for row in rows if row["source_match"] == "matched"
                    )
                response["recommendation_links"] = links
            return response
        except SessionError:
            return {**base, "error": self.error("SOURCE_SESSION_MISSING")}
        except (ProbeError, ConfigurationError, NearbyError) as failure:
            return {**base, "error": self.error(_probe_code(failure))}
        finally:
            context.lock.release()

    def identity(self, request, sessions, action="status"):
        """Purely local state; no map client methods, no deadline/counter changes."""
        base = {"context_id": request.context_id, "anchor_id": request.anchor_id,
                "request_id": request.request_id, "status": "unavailable", "revision": 0,
                "expires_in_seconds": 0, "bindings": [], "eligible_pairs": [], "reason": ""}
        context = None
        acquired = False
        try:
            context = self._context(request.context_id)
            acquired = context.lock.acquire(blocking=False)
            if not acquired:
                raise NearbyError("BUSY")
            # Recheck after admission; original map TTL never changes.
            if context.expires <= self.clock():
                raise NearbyError("CONTEXT_EXPIRED")
            return {**base, **identity_action(context, request, sessions,
                                             self.city_observations, self.clock, action)}
        except (NearbyError, IdentityError, SessionError) as failure:
            code = failure.code
            if isinstance(failure, SessionError):
                code = "SOURCE_SESSION_MISSING"
            return {**base, "error": self.error(code)}
        finally:
            if acquired:
                context.lock.release()

    def walk(self, request):
        """One explicit walk route. At most three attempts per immutable context."""
        base = {
            "context_id": request.context_id, "hotel_option_id": request.hotel_option_id,
            "provider": "amap", "data_mode": self.data_mode,
            "queried_at": observed_at(), "status": "unavailable",
            "distance_meters": None, "duration_seconds": None, "error": None,
        }
        try:
            ctx = self._context(request.context_id)
            base["provider"] = ctx.provider
            if ctx.selected is None:
                raise NearbyError("CONTEXT_NOT_SELECTED")
            if request.hotel_option_id not in ctx.hotels:
                raise NearbyError("POINT_NOT_IN_CONTEXT")
            if not ctx.lock.acquire(blocking=False):
                raise NearbyError("BUSY")
        except NearbyError as failure:
            return {**base, "error": self.error(failure.code)}
        try:
            if ctx.route_attempts >= 3:
                raise NearbyError("REQUEST_LIMIT")
            if not self.capacity.acquire(blocking=False):
                raise NearbyError("BUSY")
            ctx.route_attempts += 1  # Reserve before source request; failure still costs a call.
            try:
                self._begin_map_step(ctx)
                route = ctx.client.walk(
                    ctx.anchors[ctx.selected], ctx.hotels[request.hotel_option_id]
                )
            finally:
                self.capacity.release()
            if ctx.expires <= self.clock():
                raise NearbyError("CONTEXT_EXPIRED")
            if not route:
                raise NearbyError("ROUTE_UNAVAILABLE")
            first = route[0]
            return {
                **base, "status": "complete",
                "distance_meters": first["distance_meters"],
                "duration_seconds": first["duration_seconds"],
            }
        except (ProbeError, ConfigurationError, NearbyError) as failure:
            return {**base, "error": self.error(_probe_code(failure))}
        finally:
            ctx.lock.release()


def _probe_code(failure: Exception) -> str:
    if isinstance(failure, NearbyError):
        return failure.code
    code = getattr(failure, "code", "UNAVAILABLE")
    provider_code = getattr(failure, "provider_code", None)
    if provider_code == "121":
        return "SOURCE_DAILY_LIMIT"
    if provider_code in ("120", "10014", "10019", "10020", "10021"):
        return "RATE_LIMITED"
    return code if code in {
        "MISSING_CREDENTIAL", "INVALID_CREDENTIAL", "AUTHENTICATION_FAILED",
        "ACCESS_DENIED", "RATE_LIMITED", "REQUEST_LIMIT", "BUSY", "INVALID_ARGUMENTS", "TIMEOUT",
    } else "UNAVAILABLE"


def _join_supplier_matches(
    map_hotels: list[dict], suppliers: list[dict], query_data: dict, map_city: str
) -> None:
    """Join only when supplier search itself establishes city AND two-way unique identity.

    A supplier POI/landmark search cannot prove city from its query; do not infer
    it from a map address or copy one provider's score onto a different hotel.
    """
    if (
        query_data.get("place_type") != "城市"
        or city_name(query_data.get("place")) != city_name(map_city)
        or not _valid_region(query_data.get("country_region"))
    ):
        return
    matches = {}
    reversed_matches = {}
    for row in map_hotels:
        ids = [
            hotel["id"] for hotel in suppliers
            if (
                not query_data.get("poi_name")
                or normalized(hotel.get("address")).startswith(normalized(city_name(map_city) + "市"))
            )
            and normalized(row["name"]) == normalized(hotel.get("name"))
            and address_matches(row["address"], hotel.get("address")) is True
        ]
        matches[row["id"]] = ids
        for hotel_id in ids:
            reversed_matches[hotel_id] = reversed_matches.get(hotel_id, 0) + 1
    for row in map_hotels:
        candidates = matches[row["id"]]
        if len(candidates) == 1 and reversed_matches[candidates[0]] == 1:
            row["source_hotel_id"] = candidates[0]
            row["source_match"] = "matched"
        elif candidates:
            row["source_match"] = "ambiguous"
