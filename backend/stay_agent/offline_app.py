"""Build the fully offline 1.0 demo application from a fixed synthetic dataset.

Real provider modules stay in the repository but are never constructed here.
The existing P5/P6/P7 rule layer is reused unchanged; only its evidence input
comes from ``OfflineQuoteService`` and ``OfflineTravelService``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import json
import re
import threading
from typing import Any
from pathlib import Path
from uuid import uuid4


from .checkpoint import CheckpointError, inspect_checkpoint
from .condition_rules import merge_conditions
from .contracts import (
    ApiError,
    BookingGate,
    Cancellation,
    ConditionDraft,
    ConversationState,
    Coverage,
    DiscoveryCandidate,
    DiscoveryResponse,
    DrivingEvidence,
    FareReference,
    GateItem,
    HotelSummary,
    InterpretRequest,
    MatchAssessment,
    PlaceOption,
    Qualification,
    QuotesResponse,
    ReferenceGate,
    RoomQuote,
    SearchInput,
    SearchResponse,
    Source,
    SubwayEvidence,
    TravelPlacesResponse,
    TravelRouteRequest,
    TravelRouteResponse,
    WalkingEvidence,
    china_today,
)
from .conversation_store import ConversationError, ConversationStore
from .decision import build_quote_decision, build_route_assessment
from .intent_coverage import require_explicit_intent_coverage
from .model_output import ProposalError
from .offline_dataset import (
    CITY,
    DATASET_ID,
    DATASET_LABEL,
    DATASET_NOTICE,
    DATASET_VERSION,
    DISCOVERY_CITY_MESSAGE,
    load_dataset_bundle,
    observed_at,
)
from .pricing import QuoteInput, assess, lowest_qualified
from .storage import SessionStore
from .travel_rules import assessment

SOURCE = Source(id="rollinggo", name="道旅")
DEMO_SOURCE_NAME = DATASET_LABEL
OFFLINE_WARNING = (
    f"离线 Agent 测试夹具 · {DATASET_LABEL} v{DATASET_VERSION}；仅覆盖{CITY}；"
    "所有数据为合成值，不是实时查源、不是真实身份或可订报价，不提供真实供应商链接。"
)
OFFLINE_LIMITATIONS = (
    "本夹具因企业 MCP 权限与来源接口暂不可用而建立，只用于测试 Agent 的条件解析、资格、费用、返程与页面状态；不是真实酒店或餐厅信息。",
    "酒店与餐厅品牌为虚构样例，广州区／道路／地铁站仅提供拟真地理语境；价格、库存、评分、路线、车费与营业值全部合成。",
    "页面不会发出第三方 MCP / 地图 / 酒店 / 打车 / 模型请求；2.0 才接入真实接口。",
    "候选名单不与报价合并、去重或排序；不产生订单、锁价或预订链接。",
)
VALID_PROVIDERS = frozenset(("amap", "tencent", "didi"))


def _gate() -> ReferenceGate:
    return ReferenceGate(
        ready=True,
        items=[
            GateItem(
                field=name,
                label=label,
                status="presumed",
                note="离线夹具演示值，按国内默认推定；不是来源已核实字段。",
            )
            for name, label in (
                ("room_exclusivity", "整间客房"),
                ("private_bathroom", "房内独卫"),
                ("price_includes_tax", "展示价含税费"),
                ("occupancy_echo", "入住组合"),
            )
        ] + [
            GateItem(
                field="inventory_quantity",
                label="足量库存",
                status="presumed",
                note="离线夹具提供剩余房量观察值；不是真实库存。",
            )
        ],
    )


def _bookability() -> BookingGate:
    return BookingGate(
        ready=False,
        items=[
            GateItem(
                field=name,
                label=label,
                status="unsupported",
                note="离线 Agent 测试夹具不提供真实来源证据，此项永不放行。",
            )
            for name, label in (
                ("occupancy_echo", "入住组合回显"),
                ("nightly_breakdown", "逐夜价"),
                ("safe_booking_link", "报价级来源链接"),
            )
        ],
    )


class OfflineQuoteService:
    data_mode = "test"
    source_id = "rollinggo"

    def __init__(self, session_store: SessionStore, dataset: dict[str, Any]):
        self.sessions = session_store
        self.dataset = dataset
        self.hotels = {int(hotel["id"]): hotel for hotel in dataset["hotels"]}

    @staticmethod
    def credential_configured() -> bool:
        return True

    @staticmethod
    def error(code: str) -> ApiError:
        return ApiError(code=code, message="固定广州测试夹具暂不可用，请检查本机文件。")

    def search(self, query: SearchInput, store: SessionStore):
        request_id = "demo-" + uuid4().hex
        base = dict(
            request_id=request_id,
            context=query,
            queried_at=observed_at(),
            source=SOURCE,
            data_mode="test",
            coverage=Coverage(returned_count=0, requested_limit=query.limit, limited=False),
            warnings=[DATASET_NOTICE, OFFLINE_WARNING],
        )
        if query.place.strip() != CITY:
            base["warnings"] = [DATASET_NOTICE, DISCOVERY_CITY_MESSAGE]
            return SearchResponse(
                **base, status="unavailable",
                error=ApiError(code="DATASET_CITY_UNSUPPORTED", message=DISCOVERY_CITY_MESSAGE),
            )
        hotels: list[HotelSummary] = []
        for hotel in self.dataset["hotels"]:
            price = int(hotel["reference_price_fen"])
            if query.budget_fen is not None and price > query.budget_fen:
                continue
            hotels.append(HotelSummary(
                id=int(hotel["id"]),
                name=str(hotel["name"]),
                address=str(hotel["address"]),
                hotel_stars=float(hotel["hotel_stars"]),
                guest_score=None,
                reference_price_fen=price,
                currency="CNY",
                booking_url=None,
                unverified=[
                    "离线夹具 · 非实时房价",
                    "虚构样例 · 非真实房源",
                    "住客评分不参与门禁",
                ],
            ))
            if len(hotels) >= query.limit:
                break
        store.save(
            request_id,
            query.model_dump(mode="json"),
            [hotel.id for hotel in hotels],
            hotel_metadata=[
                {"id": hotel.id, "name": hotel.name, "address": hotel.address}
                for hotel in hotels
            ],
        )
        base["coverage"] = Coverage(
            returned_count=len(hotels),
            requested_limit=query.limit,
            limited=False,
        )
        return SearchResponse(**base, status="complete", hotels=hotels)

    def quotes(self, request_id: str, hotel_id: int, query: SearchInput) -> QuotesResponse:
        base = dict(
            request_id=request_id,
            hotel_id=hotel_id,
            hotel_name="广州样例住宿",
            context=query,
            queried_at=observed_at(),
            source=SOURCE,
            data_mode="test",
            booking_url=None,
            booking_status="unavailable",
            occupancy_status="unverified",
        )
        hotel = self.hotels.get(hotel_id)
        if hotel is None:
            return QuotesResponse(**base, status="unavailable",
                                  error=self.error("DATASET_NOT_FOUND"), warnings=[OFFLINE_WARNING])
        snapshot = observed_at()
        nights = (query.check_out - query.check_in).days
        quotes: list[RoomQuote] = []
        for index, raw in enumerate(hotel["quotations"]):
            amount = int(raw["amount_fen"])
            remaining = int(raw["remaining"])
            room_name = str(raw["room_name"])
            cancellation = str(raw["cancellation"])
            verdict = assess(QuoteInput(
                source_id="rollinggo",
                room_name=room_name,
                amount_fen=amount,
                price_unit="per_room_night",
                tax_included=True,
                on_request=False,
                remaining=remaining,
                nights=nights,
                rooms=query.rooms,
                adults=query.adults,
                children=len(query.children_ages),
                max_occupancy=query.adults + len(query.children_ages),
                cancellation_text=cancellation,
                cancellation_verified=bool(raw["cancelable"]),
                require_free_cancel=False,
                budget_fen=query.budget_fen,
                accepts_dormitory=query.accepts_dormitory,
            ))
            quotes.append(RoomQuote(
                id=f"demo-{hotel_id}-{index:02d}",
                room_name=room_name,
                plan_name=str(raw["plan_name"]),
                amount_fen=amount,
                currency="CNY",
                price_basis="supplier_reference",
                source_inventory_observed=remaining,
                cancellation=Cancellation(
                    text=cancellation,
                    can_cancel=bool(raw["cancelable"]),
                ),
                on_request=False,
                max_occupancy=query.adults + len(query.children_ages),
                unverified=["离线夹具房型 · 非真实房量", "合成费用 · 非实付总价"],
                reference=_gate(),
                qualification=Qualification.from_assessment(verdict),
            ))
        quotes.sort(key=lambda item: (item.amount_fen is None, item.amount_fen or 0, item.id))
        qualified = lowest_qualified(tuple(item.qualification for item in quotes))
        base["queried_at"] = snapshot
        base["hotel_name"] = str(hotel["name"])
        response = QuotesResponse(
            **base,
            status="complete",
            bookability=_bookability(),
            qualified_lowest_id=quotes[qualified].id if qualified is not None else None,
            quotes=quotes,
            warnings=[
                DATASET_NOTICE,
                "展示价、库存、取消规则均为 Agent 测试夹具值；「合格」只表示规则在合成输入上未发现问题，不代表可订。",
                "可订层在离线测试夹具中恒不通过：不提供入住回显、逐夜价或来源预订链接。",
            ],
        )
        response.decision = build_quote_decision(response)
        return response

    def next_page(self, request_id: str, expected_page: int, store: SessionStore):
        raise NotImplementedError("广州离线测试夹具没有分页。")


@dataclass(frozen=True)
class DemoPoint:
    poi_id: str
    title: str
    address: str
    city: str
    lat: float
    lng: float


@dataclass
class TravelContext:
    request_id: str
    hotel_id: int
    hotel: DemoPoint
    restaurant: DemoPoint
    provider: str
    options: dict[str, DemoPoint]
    expires: float
    lock: threading.Lock = field(default_factory=threading.Lock)


class OfflineTravelService:
    def __init__(self, quote_service: OfflineQuoteService, dataset: dict[str, Any]):
        self.quotes = quote_service
        self.dataset = dataset
        self.contexts: dict[str, TravelContext] = {}
        self.mutex = threading.Lock()

    @staticmethod
    def error(code: str) -> ApiError:
        return ApiError(code=code, message="广州测试夹具路线数据暂不可用，请重新检索。")

    def places(self, hotel_id: int, hotel: dict[str, Any], query: SearchInput, request):
        if request.provider not in VALID_PROVIDERS:
            return TravelPlacesResponse(
                request_id=request.request_id,
                hotel_id=hotel_id,
                hotel_name=str(hotel["name"]),
                provider=request.provider,
                queried_at=observed_at(),
                data_mode="test",
                matching=MatchAssessment(status="not_found", explanation="广州测试夹具不支持该来源。"),
                coverage_note="广州测试夹具地点列表。",
                status="unavailable",
                error=self.error("INVALID_PROVIDER"),
            )
        dataset_hotel = self.quotes.hotels.get(hotel_id)
        if dataset_hotel is None:
            return TravelPlacesResponse(
                request_id=request.request_id,
                hotel_id=hotel_id,
                hotel_name=str(hotel["name"]),
                provider=request.provider,
                queried_at=observed_at(),
                data_mode="test",
                matching=MatchAssessment(status="not_found", explanation="广州测试夹具中未找到该住宿。"),
                coverage_note="广州测试夹具地点列表。",
                status="unavailable",
                error=self.error("DATASET_NOT_FOUND"),
            )
        hotel_point = DemoPoint(
            f"demo-h-{hotel_id}", str(dataset_hotel["name"]),
            str(dataset_hotel["address"]), query.place, 23.1, 113.2,
        )
        restaurant_point = DemoPoint(
            f"demo-r-{hotel_id}", str(dataset_hotel["restaurant"]),
            str(dataset_hotel["restaurant_address"]), query.place, 23.101, 113.205,
        )
        hotel_token, restaurant_token = uuid4().hex, uuid4().hex
        options = {hotel_token: hotel_point, restaurant_token: restaurant_point}
        hotel_rows = [{"id": hotel_token, "name": hotel_point.title,
                      "address": hotel_point.address, "city": hotel_point.city}]
        rows, matched = assessment(
            {"name": hotel_point.title, "address": hotel_point.address},
            hotel_rows,
            query.place if query.place_type == "城市" else None,
            request.city,
            stay_city_basis=request.city,
        )
        context_id = uuid4().hex
        with self.mutex:
            self.contexts[context_id] = TravelContext(
                request.request_id, hotel_id, hotel_point, restaurant_point,
                request.provider, options, float("inf"),
            )
        return TravelPlacesResponse(
            request_id=request.request_id,
            hotel_id=hotel_id,
            hotel_name=hotel_point.title,
            provider=request.provider,
            context_id=context_id,
            queried_at=observed_at(),
            expires_in_seconds=3600,
            data_mode="test",
            hotels=[PlaceOption(**row) for row in rows],
            restaurants=[PlaceOption(id=restaurant_token, name=restaurant_point.title,
                                     address=restaurant_point.address, city=restaurant_point.city)],
            matching=MatchAssessment(**matched),
            coverage_note="广州测试夹具仅包含每家样例住宿对应的一个虚构餐厅门店，不代表全城覆盖。",
            status="complete",
            warnings=[DATASET_NOTICE, "地点、同店与路线均为广州测试夹具值，不是真实地图回显。"],
        )

    def route(self, hotel_id: int, request: TravelRouteRequest) -> TravelRouteResponse:
        context = self.contexts.get(request.context_id)
        if context is None or context.hotel_id != hotel_id or context.request_id != request.request_id:
            return TravelRouteResponse(
                request_id=request.request_id,
                hotel_id=hotel_id,
                provider="amap",
                queried_at=observed_at(),
                data_mode="test",
                hotel_name="广州样例住宿",
                restaurant_name="广州样例餐厅",
                hotel_match_basis="exact_fields",
                status="unavailable",
                error=self.error("TRAVEL_CONTEXT_NOT_FOUND"),
            )
        if (request.hotel_option_id not in context.options
                or request.restaurant_option_id not in context.options):
            return TravelRouteResponse(
                request_id=request.request_id,
                hotel_id=hotel_id,
                provider=context.provider,
                queried_at=observed_at(),
                data_mode="test",
                hotel_name=context.hotel.title,
                restaurant_name=context.restaurant.title,
                hotel_match_basis="exact_fields",
                status="unavailable",
                error=self.error("PLACE_NOT_IN_CONTEXT"),
            )
        row = self.quotes.hotels[hotel_id]
        walk = WalkingEvidence(
            origin_name=context.restaurant.title,
            destination_name=context.hotel.title,
            distance_meters=int(row["walk_meters"]),
            duration_seconds=int(row["walk_seconds"]),
        )
        drive = DrivingEvidence(
            origin_name=context.restaurant.title,
            destination_name=context.hotel.title,
            distance_meters=int(row["drive_meters"]),
            duration_seconds=int(row["drive_seconds"]),
        )
        subway = SubwayEvidence(
            name=str(row["subway_name"]),
            precision="named_entrance_poi",
            walk=WalkingEvidence(
                origin_name=context.hotel.title,
                destination_name=str(row["subway_name"]),
                distance_meters=int(row["subway_walk_meters"]),
                duration_seconds=int(row["subway_walk_seconds"]),
            ),
        )
        walk_status = "within_1500" if walk.distance_meters <= 1500 else "over_1500"
        drive_status = "within_900" if drive.duration_seconds <= 900 else "over_900"
        response = TravelRouteResponse(
            request_id=request.request_id,
            hotel_id=hotel_id,
            provider=context.provider,
            queried_at=observed_at(),
            data_mode="test",
            hotel_name=context.hotel.title,
            restaurant_name=context.restaurant.title,
            hotel_match_basis="exact_fields",
            return_walk=walk,
            return_drive=drive,
            walk_limit_status=walk_status,
            drive_limit_status=drive_status,
            transport_status=(
                "pass_walk_or_drive"
                if walk_status == "within_1500" or drive_status == "within_900"
                else "rejected_both_modes_over_limit"
            ),
            subway=subway,
            fares=[FareReference(
                name="普通快车（样例）",
                lower_fen=int(row["fare_lower_fen"]),
                upper_fen=int(row["fare_upper_fen"]),
            )],
            opening_today=str(row["opening_today"]),
            opening_week=str(row["opening_week"]),
            status="complete",
            warnings=[
                DATASET_NOTICE,
                "步行、驾车、地铁与车费均为广州测试夹具值；夜间入口、未来营业和实际费用未核实。",
            ],
        )
        response.return_trip = build_route_assessment(response)
        return response


class OfflineDiscoveryService:
    def __init__(self, dataset: dict[str, Any]):
        self.dataset = dataset

    def search(self, query: SearchInput) -> DiscoveryResponse:
        if query.place.strip() != CITY:
            return DiscoveryResponse(
                source=Source(id="flyai", name="飞猪"),
                status="unavailable",
                queried_at=observed_at(),
                http_used=0,
                error=ApiError(code="DATASET_CITY_UNSUPPORTED", message=DISCOVERY_CITY_MESSAGE),
                limitations=[DATASET_NOTICE, DISCOVERY_CITY_MESSAGE, *OFFLINE_LIMITATIONS],
            )
        return DiscoveryResponse(
            source=Source(id="flyai", name="飞猪"),
            status="complete",
            queried_at=observed_at(),
            observed_at=observed_at(),
            http_used=0,
            candidates=[
                DiscoveryCandidate(
                    name=str(row["name"]),
                    address=row.get("address"),
                    price_text=row.get("price_text"),
                    star=row.get("star"),
                    nearby_transit=row.get("nearby_transit"),
                    coordinates_present=False,
                )
                for row in self.dataset["discovery_candidates"]
            ],
            limitations=[DATASET_NOTICE, *OFFLINE_LIMITATIONS],
        )


class OfflineRouter:
    def resolve(self, provider: str = "deepseek"):
        from .generation import ModelRoute

        if provider != "deepseek":
            raise RuntimeError("PROVIDER_DISABLED")
        return ModelRoute("deepseek", "local-rule-parser")

    def generate(self, route, system, prompt, schema, cancelled, deadline=None):
        payload = json.loads(prompt)
        text = "\n".join(str(value) for value in payload["user_inputs"])
        return type("OfflineResult", (), {"text": demo_proposal(text, china_today())})()


def demo_proposal(text: str, today: date | None = None) -> str:
    """Deterministic local parser for a narrow set of demo phrases."""
    today = today or china_today()
    changes: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    unsupported = re.search(r"(杭州|成都|上海|深圳|北京|改去(?!广州)[^，。,\s]+)", text)
    if unsupported and "广州" not in text:
        raise ValueError("DATASET_CITY_UNSUPPORTED")
    if "广州" in text:
        changes.append({"field": "place", "value": CITY, "evidence": "广州"})
        changes.append({"field": "place_type", "value": "城市", "evidence": "广州"})
    relative = re.search(r"(大后天|后天|明天|今天)", text)
    if relative:
        offset = {"大后天": 3, "后天": 2, "明天": 1, "今天": 0}[relative.group(1)]
        start = today + timedelta(days=offset)
        changes.append({"field": "check_in", "value": start.isoformat(),
                        "evidence": relative.group(1)})
    exact_date = re.search(r"(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})日?", text)
    if exact_date and not relative:
        year = int(exact_date.group(1) or today.year)
        start = date(year, int(exact_date.group(2)), int(exact_date.group(3)))
        evidence = exact_date.group(0)
        changes.append({"field": "check_in", "value": start.isoformat(), "evidence": evidence})
    nights = re.search(r"(?:住|住\s*)?([1-9]|1\d|2[0-8])\s*晚", text)
    if nights:
        changes.append({"field": "nights", "value": int(nights.group(1)),
                        "evidence": nights.group(0)})
    rooms = re.search(r"([1-4])\s*(?:间房|间)", text)
    if rooms:
        changes.append({"field": "rooms", "value": int(rooms.group(1)),
                        "evidence": rooms.group(0)})
    adults = re.search(r"每房\s*([1-8])\s*成人|([1-8])\s*位成人", text)
    if adults:
        value = int(adults.group(1) or adults.group(2))
        changes.append({"field": "adults", "value": value, "evidence": adults.group(0)})
    if re.search(r"无儿童|没有儿童|不带儿童|0\s*儿童", text):
        evidence = re.search(r"无儿童|没有儿童|不带儿童|0\s*儿童", text).group(0)
        changes.append({"field": "children_count", "value": 0, "evidence": evidence})
    budget = re.search(r"(?:每房每晚|预算)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|人民币)", text)
    if budget:
        from decimal import Decimal

        changes.append({
            "field": "budget_fen",
            "value": int(Decimal(budget.group(1)) * 100),
            "evidence": budget.group(0),
        })
        changes.append({"field": "budget_basis", "value": "room_night",
                        "evidence": budget.group(0)})
    return json.dumps({"changes": changes, "notes": notes}, ensure_ascii=False)


class OfflineConversationService:
    """Synchronous local parser with the same commit barriers as the model path."""

    def __init__(self, store: ConversationStore):
        self.store = store
        self.lock = threading.RLock()
        self.fences: dict[str, dict[str, Any]] = {}
        self.closed = False

    def recover(self):
        self.store.recover()

    def close(self):
        self.closed = True

    def create(self):
        ident = self.store.create()
        return self.view(ident)

    def view(self, ident: str) -> ConversationState:
        record = self.store.load(ident)
        from .condition_rules import describe_conditions

        conditions, missing, question, warnings = describe_conditions(record["draft"])
        op = record["operation"] or {}
        error = None
        if op.get("error_code"):
            message = (
                DISCOVERY_CITY_MESSAGE if op["error_code"] == "DATASET_CITY_UNSUPPORTED"
                else "本地规则解析未完成，原有条件保留。"
            )
            error = ApiError(code=op["error_code"], message=message)
        return ConversationState(
            conversation_id=ident,
            revision=record["revision"],
            generation=record["generation"],
            client_sequence=record["client_sequence"],
            operation_id=op.get("id"),
            status=op.get("status", "idle"),
            draft=ConditionDraft.model_validate(record["draft"]),
            query=conditions,
            missing=missing,
            question=question,
            warnings=[*warnings, DATASET_NOTICE],
            error=error,
            provider="local",
            model="local-rule-parser",
            data_mode="test",
        )

    def start(self, ident: str, request: InterpretRequest) -> ConversationState:
        from .generation import ModelRoute

        with self.lock:
            if self.closed:
                raise ConversationError("SERVICE_CLOSED")
            if request.provider != "deepseek":
                raise ConversationError("PROVIDER_DISABLED")
            record = self.store.load(ident)
            if request.expected_revision != record["revision"]:
                raise ConversationError("REVISION_CONFLICT")
            if request.client_sequence <= record["client_sequence"]:
                raise ConversationError("STALE_OPERATION")
            fence = self.fences.setdefault(ident, {"generation": record["generation"]})
            fence["generation"] += 1
            attempt = SimpleNamespace(
                operation_id=request.operation_id,
                generation=fence["generation"],
                expected_revision=record["revision"],
                route=ModelRoute("deepseek", "local-rule-parser"),
                base_conditions=record["draft"],
                user_inputs=tuple(request.user_inputs),
                cancelled=threading.Event(),
            )
        self.store.start(ident, attempt, request.client_sequence, "local-demo")
        self.run(ident, attempt)
        return self.view(ident)

    def run(self, ident: str, attempt):
        try:
            raw = demo_proposal("\n".join(attempt.user_inputs))
            proposal = _parse_local_proposal(raw, attempt.user_inputs)
            draft = merge_conditions(attempt.base_conditions, proposal, user_inputs=attempt.user_inputs)
            require_explicit_intent_coverage(attempt.user_inputs[-1], draft)
            self.store.mark(ident, attempt, "validating")
            if not self.store.commit(ident, attempt, draft):
                raise RuntimeError("STATE_NOT_COMMITTED")
        except Exception as error:
            code = (
                "DATASET_CITY_UNSUPPORTED" if isinstance(error, ValueError)
                and error.args == ("DATASET_CITY_UNSUPPORTED",)
                else "SEMANTIC_OMISSION" if isinstance(error, ProposalError)
                and error.code == "SEMANTIC_OMISSION"
                else "INVALID_MODEL_OUTPUT" if isinstance(error, ProposalError)
                else "STATE_UNAVAILABLE" if isinstance(error, OSError)
                else "INTERNAL_ERROR"
            )
            self.store.mark(ident, attempt, "failed", code)

    def cancel(self, ident: str, request) -> ConversationState:
        with self.lock:
            record = self.store.load(ident)
            fence = self.fences.setdefault(ident, {"generation": record["generation"]})
            self.store.cancel(ident, request.operation_id, request.client_sequence,
                              fence["generation"])
            return self.view(ident)


def _parse_local_proposal(raw, user_inputs):
    from .model_output import parse_proposal

    return parse_proposal(raw, user_inputs, ())


def _iso_from_epoch(value) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _resume_search_response(provider: OfflineQuoteService, record: dict):
    sessions = record.get("resume", {}).get("search_sessions") or []
    if not sessions:
        return None, ["断点中没有可恢复的搜索会话。"]
    chosen = max(sessions, key=lambda item: float(item.get("created_at") or 0))
    try:
        context = SearchInput.model_validate(chosen["context"])
    except (KeyError, TypeError, ValueError):
        return None, ["断点中的搜索条件无法读取。"]
    if context.place.strip() != CITY:
        return None, ["断点搜索不属于当前广州测试夹具。"]
    metadata = {
        int(item["id"]): item for item in chosen.get("hotel_metadata") or [] if "id" in item
    }
    hotels: list[HotelSummary] = []
    warnings: list[str] = []
    for raw_id in chosen.get("hotel_ids") or []:
        try:
            hotel_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        dataset_hotel = provider.hotels.get(hotel_id)
        meta = metadata.get(hotel_id)
        if dataset_hotel is None:
            warnings.append("断点中的住宿已不在当前数据集，不能继续该住宿。")
            continue
        if meta and (
            meta.get("name") != dataset_hotel["name"]
            or meta.get("address") != dataset_hotel.get("address")
        ):
            warnings.append("断点住宿名称或地址与当前数据集不一致，已跳过。")
            continue
        hotels.append(HotelSummary(
            id=hotel_id,
            name=str(dataset_hotel["name"]),
            address=str(dataset_hotel["address"]),
            hotel_stars=float(dataset_hotel["hotel_stars"]),
            guest_score=None,
            reference_price_fen=int(dataset_hotel["reference_price_fen"]),
            currency="CNY",
            booking_url=None,
            unverified=["离线夹具 · 非实时房价", "断点恢复 · 合成演示值", "住客评分不参与门禁"],
        ))
    if not hotels:
        return None, warnings + ["断点中没有可与当前数据集继续的住宿。"]
    return SearchResponse(
        request_id=str(chosen["request_id"]),
        context=context,
        queried_at=_iso_from_epoch(chosen.get("created_at"))
        or record.get("saved_at") or record.get("disconnected_at"),
        source=SOURCE,
        data_mode="test",
        coverage=Coverage(
            returned_count=len(hotels), requested_limit=context.limit, limited=False
        ),
        status="complete",
        hotels=hotels,
        warnings=[DATASET_NOTICE, OFFLINE_WARNING, "断点恢复：使用断时快照中的已提交搜索和当前版本数据集重建，不是新的查源。"],
    ), warnings


def build_checkpoint_resume(provider, conversation, root, label, runtime_root=None) -> dict:
    """Return the committed search/draft pointers for the latest validated checkpoint."""
    try:
        record = inspect_checkpoint(root, label, runtime_root=runtime_root)
    except CheckpointError as error:
        return {
            "status": "unavailable",
            "checkpoint_id": label,
            "saved_at": None,
            "disconnected_at": None,
            "search": None,
            "conversation": None,
            "warnings": ["断点校验失败，已保留现场，未继续使用旧状态。"],
            "error": {"code": str(error), "message": "断点记录不可恢复，请核对后再试。"},
        }
    search, warnings = _resume_search_response(provider, record)
    draft = None
    conversations = record.get("resume", {}).get("conversations") or []
    for item in sorted(conversations, key=lambda row: int(row.get("revision") or 0), reverse=True):
        try:
            draft = conversation.view(item["conversation_id"])
            break
        except ConversationError:
            warnings.append("断点中的条件草稿已过期或不在当前运行目录，需重新开始。")
    return {
        "status": "ready" if search is not None or draft is not None else "unavailable",
        "checkpoint_id": record.get("checkpoint_id"),
        "saved_at": record.get("saved_at") or record.get("disconnected_at"),
        "disconnected_at": record.get("disconnected_at"),
        "search": search,
        "conversation": draft,
        "warnings": warnings,
        "error": None if search is not None or draft is not None else {
            "code": "NO_RESUMABLE_STATE", "message": "断点中的搜索和条件草稿都无法继续。"
        },
    }


def build_offline_app(root=None, *, web_root=None, runtime_root=None):
    """Construct the offline demo app without importing live service modules."""
    from .main import create_app

    dataset = load_dataset_bundle()
    if root is None:
        from .conversation import ROOT as project_root

        root = project_root
    state_root = Path(runtime_root) if runtime_root is not None else root / "runtime"
    sessions = SessionStore(state_root / "offline_search.sqlite3")
    conversations = ConversationStore(state_root / "offline_conversations.sqlite3")
    quote = OfflineQuoteService(sessions, dataset)
    travel = OfflineTravelService(quote, dataset)
    app = create_app(
        service=quote,
        session_store=sessions,
        web_root=web_root,
        travel_service=travel,
        conversation_service=OfflineConversationService(conversations),
        recall_service=_OfflineUnavailable(),
        branch_walk_service=_OfflineUnavailable(),
        discovery_service=OfflineDiscoveryService(dataset),
        offline_demo=True,
    )
    app.state.data_mode = "offline"
    app.state.offline_demo = True
    app.state.runtime_root = state_root
    app.state.dataset_id = DATASET_ID
    return app


class _OfflineUnavailable:
    def __getattr__(self, name):
        def unavailable(*args, **kwargs):
            raise RuntimeError("OFFLINE_DEMO_UNSUPPORTED_WORKFLOW")
        return unavailable
