"""Lazy location resolution and current return references, bound to a price search."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from uuid import uuid4

from stay_agent.providers.data.rollinggo import ConfigurationError
from stay_agent.providers.data.rollinggo_client import RollingGoError
from stay_agent.providers.data.travel_clients import (
    GEOCODE_PRECISE_LEVELS,
    DidiDiscovery,
    MapProbe,
    ProbeError,
    didi_places,
    didi_estimate,
    distance_meters,
    load_key,
)
from .contracts import (
    ApiError,
    DrivingEvidence,
    FareReference,
    MatchAssessment,
    PlaceOption,
    TravelPlacesResponse,
    TravelRouteResponse,
    SubwayEvidence,
    WalkingEvidence,
)
from .travel_rules import assessment, city_name, entrance_name, fare_amount

PROVIDERS = {"amap": "高德", "tencent": "腾讯", "didi": "滴滴"}

# Identity assistance is deliberately narrow: one geocode call, only when a
# single candidate already agrees on name and city, and only a precise-level
# address resolution within this distance counts as the same place.
GEOCODE_MATCH_METERS = 150


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, maximum=300):
    return value.strip()[:maximum] if isinstance(value, str) and value.strip() else None


def short_query(value):
    value = value[:80]
    while len(value.encode("utf-8")) > 96:
        value = value[:-1]
    return value


@dataclass
class TravelContext:
    request_id: str
    hotel_id: int
    hotel_name: str
    provider: str
    client: object
    hotels: dict
    restaurants: dict
    match: dict
    expires: float
    lock: object = field(default_factory=threading.Lock)
    destination_search: bool = False


class TravelError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class TravelService:
    def __init__(self, factories=None, clock=time.monotonic, data_mode="live"):
        self.factories = factories or {
            "amap": lambda: MapProbe("amap", load_key("amap")),
            "tencent": lambda: MapProbe("tencent", load_key("tencent")),
            "didi": lambda: DidiDiscovery(load_key("didi")),
        }
        self.clock = clock
        self.data_mode = data_mode
        self.contexts = {}
        self.mutex = threading.Lock()
        self.capacity = threading.BoundedSemaphore(2)

    @staticmethod
    def error(error):
        code = error.code if hasattr(error, "code") else "UNAVAILABLE"
        provider_code = getattr(error, "provider_code", None)
        if provider_code == "121":
            code = "SOURCE_DAILY_LIMIT"
        elif provider_code in ("120", "10014", "10019", "10020", "10021"):
            code = "RATE_LIMITED"
        messages = {
            "MISSING_CREDENTIAL": "此来源尚未配置，请先完成本机配置。",
            "INVALID_CREDENTIAL": "来源配置格式无效，请在本机检查。",
            "AUTHENTICATION_FAILED": "来源认证失败，请在本机检查配置。",
            "ACCESS_DENIED": "来源尚未授权此查询。",
            "SOURCE_DAILY_LIMIT": "来源日额度已达上限，请检查该Key的额度。",
            "RATE_LIMITED": "来源请求较频繁，请稍后重新查询。",
            "BUSY": "当前查询较多，请稍后重试。",
            "TIMEOUT": "来源查询超时，请重新查询。",
            "REQUEST_LIMIT": "本次地点上下文已达到请求上限，请重新检索地点。",
            "INVALID_ARGUMENTS": "请检查城市和地点关键词。",
        }
        return ApiError(
            code=code, message=messages.get(code, "暂未取得可核实的位置或出行信息，请重试。")
        )

    def _rows(self, client, points):
        options = []
        refs = {}
        for point in points:
            token = uuid4().hex
            meta = client.metadata.get(point.poi_id, {})
            options.append(
                {
                    "id": token,
                    "name": point.title,
                    "address": text(meta.get("address")),
                    "city": text(meta.get("city"), 50),
                }
            )
            refs[token] = point
        return options, refs

    @staticmethod
    def _geocode_identity(client, hotel, hotel_options, hotel_refs, matched, city):
        """Confirm one plausible candidate by same-source geocode distance.

        Guard rails: only when the field check already agrees on name and city
        for exactly one candidate, at most one geocode request, and only a
        precise-level address resolution within GEOCODE_MATCH_METERS counts.
        Every other outcome keeps the original unverified verdict.
        """
        if matched["status"] != "unverified":
            return matched
        address = hotel.get("address")
        if not isinstance(address, str) or not address.strip():
            return matched
        eligible = [
            index
            for index, option in enumerate(hotel_options)
            if option.get("name_match") is True and option.get("city_match") is True
        ]
        if len(eligible) != 1:
            return matched
        index = eligible[0]
        point = hotel_refs.get(hotel_options[index]["id"])
        if point is None:
            return matched
        geocode = getattr(client, "geocode", None)
        if geocode is None:
            return matched
        try:
            results = geocode(address, city)
        except ProbeError:
            return matched
        precise = [row for row in results if row["level"] in GEOCODE_PRECISE_LEVELS]
        if not precise:
            return matched
        nearest = min(
            precise,
            key=lambda row: distance_meters(row["lng"], row["lat"], point.lng, point.lat),
        )
        distance = distance_meters(nearest["lng"], nearest["lat"], point.lng, point.lat)
        if distance > GEOCODE_MATCH_METERS:
            return matched
        hotel_options[index]["address_match"] = True
        return {
            "status": "matched",
            "selected_id": hotel_options[index]["id"],
            "basis": "geocode",
            "explanation": (
                "名称与城市一致；来源地址经同一地图来源地理编码核对，"
                f"距该候选约 {round(distance)} 米（阈值 {GEOCODE_MATCH_METERS} 米），唯一命中。"
            ),
        }

    def places(self, hotel_id, hotel, query, request):
        base = dict(
            request_id=request.request_id,
            hotel_id=hotel_id,
            hotel_name=hotel["name"],
            provider=request.provider,
            queried_at=now(),
            data_mode=self.data_mode,
            matching=MatchAssessment(status="not_found", explanation="尚未取得地点匹配结果。"),
            coverage_note="仅本次来源返回范围，不代表全城完整结果。",
        )
        if not self.capacity.acquire(blocking=False):
            return TravelPlacesResponse(
                **base, status="unavailable", error=self.error(TravelError("BUSY"))
            )
        try:
            client = self.factories[request.provider]()
            keyword = short_query(hotel["name"])
            destination_search = request.destination_keyword is not None
            origin = short_query(request.destination_keyword) if destination_search else request.restaurant_keyword
            if request.provider == "didi":
                hotels = didi_places(client, keyword, request.city)[:5]
                restaurants = didi_places(client, origin, request.city)[:20]
                coverage = "滴滴返回的当前地点候选；住宿最多5条、门店最多20条，不代表全城完整。"
            else:
                hotels = client.search(keyword, request.city, page_size=5)
                restaurants = []
                seen = set()
                for page in (1, 2):
                    points = client.search(
                        origin, request.city, page=page, page_size=10
                    )
                    new = [p for p in points if p.poi_id not in seen]
                    restaurants.extend(new)
                    seen.update(p.poi_id for p in new)
                    if not new or len(points) < 10:
                        break
                coverage = "门店最多检索前2页20条；住宿最多5条，不代表全城完整。"
            hotel_options, hotel_refs = self._rows(client, hotels)
            restaurant_options, restaurant_refs = self._rows(client, restaurants)
            if destination_search:
                restaurant_options = [
                    option for option in restaurant_options
                    if not option["city"] or city_name(option["city"]) == city_name(request.city)
                ]
                restaurant_refs = {option["id"]: restaurant_refs[option["id"]] for option in restaurant_options}
            original_city = query.place if query.place_type == "城市" else None
            hotel_options, matched = assessment(
                hotel,
                hotel_options,
                original_city,
                request.city,
                stay_city_basis=request.city,
            )
            if request.provider == "amap":
                matched = self._geocode_identity(
                    client, hotel, hotel_options, hotel_refs, matched, request.city
                )
            token = uuid4().hex
            context = TravelContext(
                request.request_id,
                hotel_id,
                hotel["name"],
                request.provider,
                client,
                hotel_refs,
                restaurant_refs,
                matched,
                self.clock() + 120,
                destination_search=destination_search,
            )
            with self.mutex:
                self.contexts = {
                    key: value
                    for key, value in self.contexts.items()
                    if value.expires > self.clock()
                }
                if len(self.contexts) >= 64:
                    raise TravelError("BUSY")
                self.contexts[token] = context
            base.update(
                context_id=token,
                queried_at=now(),
                hotels=[PlaceOption(**v) for v in hotel_options],
                restaurants=[PlaceOption(**v) for v in restaurant_options],
                matching=MatchAssessment(**matched),
                coverage_note=coverage,
            )
            warnings = ["路线和估价为当前参考；夜间入口、营业覆盖和住宿完整费用尚未核实。"]
            if keyword != hotel["name"]:
                warnings.append("住宿名称较长，本次检索使用了截短关键词，请核对完整名称与地址。")
            if request.provider == "didi":
                warnings.append(
                    "滴滴地点与地图来源分别解析，不能混用坐标；车费不保证适用未来返程时段。"
                )
            return TravelPlacesResponse(
                **base,
                status="complete" if hotel_options and restaurant_options else "partial",
                warnings=warnings,
            )
        except (ProbeError, RollingGoError, ConfigurationError, TravelError) as error:
            base["queried_at"] = now()
            return TravelPlacesResponse(**base, status="unavailable", error=self.error(error))
        finally:
            self.capacity.release()

    def context(self, hotel_id, request):
        with self.mutex:
            context = self.contexts.get(request.context_id)
        if context is None:
            raise TravelError("TRAVEL_CONTEXT_NOT_FOUND")
        if context.expires <= self.clock():
            with self.mutex:
                self.contexts.pop(request.context_id, None)
            raise TravelError("TRAVEL_CONTEXT_EXPIRED")
        if context.request_id != request.request_id or context.hotel_id != hotel_id:
            raise TravelError("TRAVEL_CONTEXT_MISMATCH")
        if (
            request.hotel_option_id not in context.hotels
            or request.restaurant_option_id not in context.restaurants
        ):
            raise TravelError("PLACE_NOT_IN_CONTEXT")
        exact = (
            context.match["status"] == "matched"
            and context.match["selected_id"] == request.hotel_option_id
        )
        if not exact and not request.confirm_hotel:
            raise TravelError("HOTEL_CONFIRMATION_REQUIRED")
        return context, exact

    @staticmethod
    def _walk(origin, destination, routes):
        if not routes:
            return None
        route = routes[
            0
        ]  # One whole source route; never mix duration/distance from different plans.
        return WalkingEvidence(
            origin_name=origin.title,
            destination_name=destination.title,
            distance_meters=route["distance_meters"],
            duration_seconds=route["duration_seconds"],
        )

    def route(self, hotel_id, request):
        context, exact = self.context(hotel_id, request)
        hotel = context.hotels[request.hotel_option_id]
        restaurant = context.restaurants[request.restaurant_option_id]
        base = dict(
            request_id=request.request_id,
            hotel_id=hotel_id,
            provider=context.provider,
            hotel_name=hotel.title,
            restaurant_name=restaurant.title,
            queried_at=now(),
            data_mode=self.data_mode,
            hotel_match_basis="exact_fields" if exact else "user_confirmed",
        )
        if not context.lock.acquire(blocking=False):
            return TravelRouteResponse(
                **base, status="unavailable", error=self.error(TravelError("BUSY"))
            )
        if not self.capacity.acquire(blocking=False):
            context.lock.release()
            return TravelRouteResponse(
                **base, status="unavailable", error=self.error(TravelError("BUSY"))
            )
        try:
            client = context.client
            # A new explicit user action gets a fresh deadline, while total request counts remain bounded.
            if hasattr(client, "http"):
                client.http.deadline = None
            if context.provider == "didi" and hasattr(client, "rpc"):
                client.rpc._deadline = None
            if context.provider == "didi":
                data = didi_estimate(client, restaurant, hotel)
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    raise ProbeError("INVALID_FARES")
                fares = []
                for item in items[:30]:
                    if not isinstance(item, dict):
                        continue
                    low, high = fare_amount(item.get("priceText"))
                    fares.append(
                        FareReference(
                            name=text(item.get("productName"), 50) or "车型待核实",
                            lower_fen=low,
                            upper_fen=high,
                        )
                    )
                base["queried_at"] = now()
                return TravelRouteResponse(
                    **base,
                    status="complete" if any(f.upper_fen is not None for f in fares) else "partial",
                    fares=fares,
                    warnings=[
                        "当前车费参考，实际费用可能变化或超过预算；不代表未来深夜价格。",
                        "来源未提供载客上限、优惠适用资格与明确报价有效期，不能据此认定满足返程硬条件。",
                    ],
                )
            walk = self._walk(restaurant, hotel, client.walk(restaurant, hotel))
            if walk is None and not context.destination_search:
                raise ProbeError("INVALID_ROUTES")
            origin_label = "目的地" if context.destination_search else "餐厅"
            warnings = [
                f"步行按{origin_label}→住宿方向；按来源地图地点计算，实际出入口与夜间通道仍需核实。",
                "交通准入按步行距离≤1500米或驾车预计≤900秒任一满足；驾车不含司机接驾/候车，不代表未来路况。",
                "路线和营业信息不是未来日期或夜间接待保证；完整住宿与返程总成本尚不能计算。",
            ]
            walk_limit_status = "unverified"
            if walk is not None:
                walk_limit_status = "within_1500" if walk.distance_meters <= 1500 else "over_1500"
            else:
                warnings.append("来源未返回步行路线。")
            return_drive = None
            drive_limit_status = "unverified"
            subway = None
            partial = walk is None
            # Same OR rule as the branch-walk flow: skip driving when walking
            # already qualifies, otherwise ask the same map source for driving.
            if walk_limit_status != "within_1500":
                try:
                    drive_route = self._walk(
                        restaurant, hotel, client.drive(restaurant, hotel)
                    )
                    if drive_route is None:
                        partial = True
                        warnings.append("驾车路线未取得，暂不能判断驾车时限。")
                    else:
                        return_drive = DrivingEvidence(
                            origin_name=drive_route.origin_name,
                            destination_name=drive_route.destination_name,
                            distance_meters=drive_route.distance_meters,
                            duration_seconds=drive_route.duration_seconds,
                        )
                        drive_limit_status = (
                            "within_900"
                            if drive_route.duration_seconds <= 900
                            else "over_900"
                        )
                except (ProbeError, RollingGoError) as error:
                    partial = True
                    warnings.append("驾车路线未取得：" + self.error(error).message)
            transport_status = (
                "pass_walk_or_drive"
                if walk_limit_status == "within_1500"
                or drive_limit_status == "within_900"
                else "rejected_both_modes_over_limit"
                if walk_limit_status == "over_1500"
                and drive_limit_status == "over_900"
                else "unknown_or_partial"
            )
            if not context.destination_search:
                try:
                    stations = client.around(hotel, "地铁站出入口", limit=3)
                    entrance = next((p for p in stations if entrance_name(p.title)), None)
                    station = entrance or (stations[0] if stations else None)
                    if station:
                        transit_walk = self._walk(hotel, station, client.walk(hotel, station))
                        if transit_walk:
                            subway = SubwayEvidence(
                                name=station.title,
                                precision="named_entrance_poi" if entrance else "station_poi",
                                walk=transit_walk,
                            )
                    if subway is None:
                        partial = True
                        warnings.append("本次检索范围内未取得地铁步行证据，不代表附近一定没有地铁。")
                except (ProbeError, RollingGoError) as error:
                    partial = True
                    warnings.append("地铁信息未取得：" + self.error(error).message)
            meta = client.metadata.get(restaurant.poi_id, {})
            base["queried_at"] = now()
            return TravelRouteResponse(
                **base,
                status="partial" if partial else "complete",
                return_walk=walk,
                return_drive=return_drive,
                walk_limit_status=walk_limit_status,
                drive_limit_status=drive_limit_status,
                transport_status=transport_status,
                subway=subway,
                opening_today=text(meta.get("open_today")),
                opening_week=text(meta.get("open_week")),
                warnings=warnings,
            )
        except (ProbeError, RollingGoError, ConfigurationError) as error:
            base["queried_at"] = now()
            return TravelRouteResponse(**base, status="unavailable", error=self.error(error))
        finally:
            self.capacity.release()
            context.lock.release()
