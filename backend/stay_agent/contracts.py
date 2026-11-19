"""Typed quote-browser contract. No complete-stay or fare total is inferred."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

Fen = Annotated[StrictInt, Field(ge=0, le=100000000)]
Age = Annotated[StrictInt, Field(ge=0, le=17)]


def china_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchInput(Model):
    place: str = Field(min_length=1, max_length=120)
    place_type: Literal["城市", "景点", "地铁站", "区/县", "详细地址", "酒店", "火车站", "机场"] = (
        "城市"
    )
    check_in: date
    check_out: date
    adults: Annotated[StrictInt, Field(ge=1, le=8)] = 2
    rooms: Annotated[StrictInt, Field(ge=1, le=4)] = 1
    children_ages: list[Age] = Field(default_factory=list, max_length=4)
    budget_fen: Fen | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=20)] = 20
    # Optional restaurant/POI anchor: when present, the source search is
    # narrowed to hotels around this point instead of the whole city. The
    # source's own radius and distance semantics stay unverified until a real
    # response proves them.
    poi_name: str | None = Field(default=None, max_length=120)
    # Explicit geography helps disambiguate similarly named destinations.
    # Waypoints are itinerary notes, never unverified map coordinates or supplier filters.
    country_region: str | None = Field(default=None, max_length=80)
    waypoints: list[str] = Field(default_factory=list, max_length=5)
    #: The user explicitly said a bed place (dormitory / bunk) is acceptable.
    #: Default false, because a bed is not a whole overnight room and the room
    #: form is a hard condition. When true it relaxes exactly two conditions --
    #: whole room and private bathroom -- and only for room names that say so.
    accepts_dormitory: bool = False

    @field_validator("place")
    @classmethod
    def clean_place(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("place is required")
        return value

    @field_validator("poi_name")
    @classmethod
    def clean_poi_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("poi_name contains control characters")
        return value

    @field_validator("country_region")
    @classmethod
    def clean_country_region(cls, value: str | None) -> str | None:
        # Legacy requests may omit country_region; explicitly overseas
        # requests are invalid BEFORE any hotel supplier or map request.
        if value is None:
            return None
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("country_region contains control characters")
        if value and value.casefold() not in {
            "中国", "中国大陆", "中华人民共和国", "china", "mainland china", "prc",
        }:
            raise ValueError("only mainland China destinations are supported")
        return value or None

    @field_validator("waypoints")
    @classmethod
    def clean_waypoints(cls, value: list[str]) -> list[str]:
        cleaned = []
        seen = set()
        for raw in value:
            stop = raw.strip()
            if not stop or len(stop) > 120 or any(ord(char) < 32 or ord(char) == 127 for char in stop):
                raise ValueError("invalid waypoint")
            key = stop.casefold()
            if key in seen:
                raise ValueError("duplicate waypoint")
            seen.add(key)
            cleaned.append(stop)
        return cleaned

    @model_validator(mode="after")
    def dates_are_valid(self, info: ValidationInfo):
        today = (info.context or {}).get("today") or china_today()
        if self.check_in < today:
            raise ValueError("check-in must not be in the past")
        if not 1 <= (self.check_out - self.check_in).days <= 28:
            raise ValueError("stay must be between 1 and 28 nights")
        return self


class QuoteRequest(Model):
    request_id: str = Field(min_length=1, max_length=64)


class SearchPageRequest(Model):
    request_id: str = Field(min_length=1, max_length=64)
    expected_page: StrictInt = Field(ge=2, le=3)


class Source(Model):
    id: Literal["rollinggo", "tuniu", "flyai"] = "rollinggo"
    name: Literal["道旅", "途牛", "飞猪"] = "道旅"

    @model_validator(mode="after")
    def valid_pair(self):
        if (self.id, self.name) not in (
            ("rollinggo", "道旅"),
            ("tuniu", "途牛"),
            ("flyai", "飞猪"),
        ):
            raise ValueError("source id/name mismatch")
        return self


class ApiError(Model):
    code: str
    message: str


class GuestScore(Model):
    value: float
    scale: float
    source: str


class HotelSummary(Model):
    id: StrictInt
    name: str
    address: str | None = None
    image_url: str | None = None
    hotel_stars: float | None = None
    guest_score: GuestScore | None = None
    reference_price_fen: Fen | None = None
    currency: str | None = None
    booking_url: str | None = None
    unverified: list[str] = Field(default_factory=list)


class Coverage(Model):
    returned_count: int
    requested_limit: int
    limited: bool


class SearchResponse(Model):
    request_id: str
    status: Literal["complete", "partial", "unavailable"]
    context: SearchInput
    queried_at: str
    source: Source = Field(default_factory=Source)
    data_mode: Literal["live", "test"] = "live"
    hotels: list[HotelSummary] = Field(default_factory=list)
    coverage: Coverage
    warnings: list[str] = Field(default_factory=list)
    error: ApiError | None = None
    # Page number only: supplier queryId is never serialized to clients.
    current_page: int = Field(default=1, ge=1, le=3)
    next_page: int | None = Field(default=None, ge=2, le=3)


class Cancellation(Model):
    text: str = "取消规则待核实"
    can_cancel: bool | None = None


class DiscoveryCandidate(Model):
    """A candidate *name* from a discovery source.

    It carries no room, no inventory and no money amount: ``price_text`` is the
    supplier's verbatim display string, kept as text on purpose so the page can
    never turn it into a comparable price. Coordinates are reduced to a
    boolean for the same reason -- they must not feed a distance or route.
    """

    name: str
    address: str | None = None
    price_text: str | None = None
    star: str | None = None
    nearby_transit: str | None = None
    coordinates_present: bool = False


class DiscoveryResponse(Model):
    """One discovery source's candidate list. Never merged, ranked or bookable."""

    source: Source
    status: Literal[
        "complete", "partial", "limited", "unavailable",
        "unconfigured", "expired", "cancelled",
    ]
    queried_at: str
    observed_at: str | None = None
    http_used: int = Field(default=0, ge=0, le=8)
    candidates: list[DiscoveryCandidate] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error: ApiError | None = None


class GateItem(Model):
    """One evidence row for the reference layer, already labelled in Chinese."""

    field: str
    label: str
    status: Literal["verified", "presumed", "accepted", "unknown", "unsupported", "blocked"]
    note: str = ""


def _project_gate(gate) -> list[GateItem]:
    """Rule-layer ``LayerGate`` -> wire rows. The only such projection.

    Every source and every layer must go through this one function, so no
    caller can invent a friendlier status or drop an inconvenient row.
    """
    from .qualification import gate_items

    return [
        GateItem(field=row.field, label=row.label, status=row.status, note=row.note)
        for row in gate_items(gate)
    ]


class ReferenceGate(Model):
    """Reference-layer verdict for a single room quote.

    ``ready`` only means "this price may be shown as a clearly labelled
    reference". It never means bookable: the booking layer rejects every
    presumption, and no source currently passes it.
    """

    ready: bool
    items: list[GateItem] = Field(default_factory=list)

    @classmethod
    def from_gate(cls, gate) -> "ReferenceGate":
        return cls(ready=gate.ready, items=_project_gate(gate))


class BookingGate(Model):
    """Booking-layer verdict for a whole quotes response.

    ``ready`` is the *only* thing that may license an "可订／实付价" claim.
    The booking layer accepts verified fields only, so it is false for every
    source today: no supplier echoes the occupancy combination, and none
    returns a per-quote deposit or a verbatim link.

    ``None`` on the response means "not evaluated" and must be shown as
    unknown — never as a pass, and never as bookable.
    """

    ready: bool
    items: list[GateItem] = Field(default_factory=list)

    @classmethod
    def from_gate(cls, gate) -> "BookingGate":
        return cls(ready=gate.ready, items=_project_gate(gate))


class BudgetAssessmentWire(Model):
    """Budget check, with the basis it was measured on.

    ``average_presumed`` may pass (user decision 2026-11-07) but its detail
    always carries the risk that one night could exceed the budget; the page
    must print that detail verbatim and never upgrade it to "every night OK".
    """

    verdict: Literal["pass", "fail", "unknown"]
    basis: Literal[
        "no_budget",
        "single_night_known_costs",
        "nightly_verified",
        "average_presumed",
        "nightly_incomplete",
        "total_unknown",
    ]
    detail: str
    budget_fen: Fen | None = None
    compared_fen: Fen | None = None


class QualifiedCondition(Model):
    condition: str
    label: str
    verdict: Literal["pass", "fail", "unknown"]
    basis: str
    detail: str
    presumed: bool = False
    #: The user explicitly relaxed this condition (bed place accepted). Kept
    #: separate from ``presumed`` so the page cannot print our guess and the
    #: user's decision with the same word.
    accepted: bool = False


class FeeLineWire(Model):
    kind: Literal["room_charge", "mandatory_fee", "deposit", "discount"]
    label: str
    amount_fen: Fen
    per: Literal["stay", "room_night", "room_stay", "person_night"]
    total_fen: Fen
    counted: bool
    note: str = ""


class FeeBreakdownWire(Model):
    """Every known cost, every unverified one, and the sum of the known part.

    ``total_known_fen`` is deliberately not called "total": it excludes
    anything unverified. A page that prints it as an all-in figure is lying,
    which is why ``unknown_items`` and ``tax_basis`` travel with it.
    """

    nights: StrictInt
    rooms: StrictInt
    room_charge_fen: Fen | None = None
    mandatory_fees_fen: Fen = 0
    deposit_fen: Fen | None = None
    deposit_refundable: bool | None = None
    discount_fen: Fen | None = None
    discount_applied_fen: Fen = 0
    total_known_fen: Fen | None = None
    per_room_night_fen: Fen | None = None
    tax_basis: str
    unknown_items: list[str] = Field(default_factory=list)
    lines: list[FeeLineWire] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Qualification(Model):
    """Deterministic qualification verdict for one quote.

    ``qualified`` means every condition passed and the budget did not fail.
    Presumptions (whole room, private bathroom, tax included) are allowed here
    but each one is flagged, and ``tier`` never licenses a booking claim.
    """

    tier: Literal["qualified", "reference", "excluded"]
    conditions: list[QualifiedCondition] = Field(default_factory=list)
    fees: FeeBreakdownWire
    budget: BudgetAssessmentWire
    reasons: list[str] = Field(default_factory=list)

    @classmethod
    def from_assessment(cls, assessment) -> "Qualification":
        """The only rule -> wire projection for qualification."""
        return cls(
            tier=assessment.tier,
            conditions=[
                QualifiedCondition(
                    condition=row.condition, label=row.label, verdict=row.verdict,
                    basis=row.basis, detail=row.detail, presumed=row.presumed,
                    accepted=row.accepted,
                )
                for row in assessment.conditions
            ],
            fees=FeeBreakdownWire(
                nights=assessment.fees.nights,
                rooms=assessment.fees.rooms,
                room_charge_fen=assessment.fees.room_charge_fen,
                mandatory_fees_fen=assessment.fees.mandatory_fees_fen,
                deposit_fen=assessment.fees.deposit_fen,
                deposit_refundable=assessment.fees.deposit_refundable,
                discount_fen=assessment.fees.discount_fen,
                discount_applied_fen=assessment.fees.discount_applied_fen,
                total_known_fen=assessment.fees.total_known_fen,
                per_room_night_fen=assessment.fees.per_room_night_fen,
                tax_basis=assessment.fees.tax_basis,
                unknown_items=list(assessment.fees.unknown_items),
                lines=[
                    FeeLineWire(
                        kind=line.kind, label=line.label, amount_fen=line.amount_fen,
                        per=line.per, total_fen=line.total_fen, counted=line.counted,
                        note=line.note,
                    )
                    for line in assessment.fees.lines
                ],
                notes=list(assessment.fees.notes),
            ),
            budget=BudgetAssessmentWire(
                verdict=assessment.budget.verdict,
                basis=assessment.budget.basis,
                detail=assessment.budget.detail,
                budget_fen=assessment.budget.budget_fen,
                compared_fen=assessment.budget.compared_fen,
            ),
            reasons=list(assessment.reasons),
        )


class ReturnTripConditionWire(Model):
    """One P7 hard-gate condition. ``unknown`` is never a pass."""

    key: str
    label: str
    verdict: Literal["pass", "fail", "unknown"]
    basis: str
    detail: str


class PlanAssessmentWire(Model):
    """Deterministic return-trip verdict for one stay plan."""

    plan_id: str
    tier: Literal["rankable", "reference_only", "excluded"]
    conditions: list[ReturnTripConditionWire] = Field(default_factory=list)
    stay_known_fen: Fen | None = None
    fare_used_fen: Fen | None = None
    total_known_fen: Fen | None = None
    guest_score_tenths: StrictInt | None = None
    review_count: StrictInt | None = None
    notes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @classmethod
    def from_assessment(cls, assessment) -> "PlanAssessmentWire":
        return cls(
            plan_id=assessment.plan_id,
            tier=assessment.tier,
            conditions=[
                ReturnTripConditionWire(
                    key=row.key, label=row.label, verdict=row.verdict,
                    basis=row.basis, detail=row.detail,
                )
                for row in assessment.conditions
            ],
            stay_known_fen=assessment.stay_known_fen,
            fare_used_fen=assessment.fare_used_fen,
            total_known_fen=assessment.total_known_fen,
            guest_score_tenths=assessment.guest_score_tenths,
            review_count=assessment.review_count,
            notes=list(assessment.notes),
            risks=list(assessment.risks),
        )


class RankedPlanWire(Model):
    plan_id: str
    position: StrictInt
    guest_score_tenths: StrictInt
    review_count: StrictInt
    total_known_fen: Fen | None = None
    tie_break: str


class DecisionWire(Model):
    """P7 hard gate + ranking for one response.

    ``status='not_comparable'`` is a real outcome, not a failure to render:
    it means the ranking fields were not supplied by the source. The page must
    show ``reasons`` and must never silently order by total cost instead.
    """

    status: Literal["ranked", "not_comparable"]
    order: list[RankedPlanWire] = Field(default_factory=list)
    excluded_ids: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    assessments: list[PlanAssessmentWire] = Field(default_factory=list)
    ranking_basis: str
    evidence_binding: str
    data_mode: Literal["live", "test"] = "live"

    @classmethod
    def from_result(
        cls, result, assessments, *, evidence_binding: str,
        data_mode: Literal["live", "test"] = "live",
    ) -> "DecisionWire":
        return cls(
            status=result.status,
            order=[
                RankedPlanWire(
                    plan_id=row.plan_id,
                    position=row.position,
                    guest_score_tenths=row.guest_score_tenths,
                    review_count=row.review_count,
                    total_known_fen=row.total_known_fen,
                    tie_break=row.tie_break,
                )
                for row in result.order
            ],
            excluded_ids=list(result.excluded_ids),
            reference_ids=list(result.reference_ids),
            reasons=list(result.reasons),
            assessments=[PlanAssessmentWire.from_assessment(row) for row in assessments],
            ranking_basis="住客评分 → 评价人数 → 已知费用小计 → 输入顺序",
            evidence_binding=evidence_binding,
            data_mode=data_mode,
        )


class RoomQuote(Model):
    id: str
    room_name: str
    plan_name: str | None = None
    amount_fen: Fen | None = None
    currency: str | None = None
    price_basis: Literal["supplier_average", "supplier_reference"] = "supplier_average"
    source_inventory_observed: int | None = Field(default=None, ge=0, le=100000)
    bed_type: str | None = None
    meal: str | None = None
    cancellation: Cancellation = Field(default_factory=Cancellation)
    on_request: bool | None = None
    max_occupancy: int | None = None
    room_size_sqm: float | None = None
    unverified: list[str] = Field(default_factory=list)
    #: Reference-layer gate for this quote. ``None`` means "not evaluated" and
    #: must be shown as unknown, never as a pass.
    reference: ReferenceGate | None = None
    #: P6 qualification + fee verdict. ``None`` means "not evaluated" and must
    #: never be read as qualified.
    qualification: Qualification | None = None


class QuotesResponse(Model):
    request_id: str
    hotel_id: int
    hotel_name: str
    status: Literal["complete", "partial", "unavailable"]
    context: SearchInput
    queried_at: str
    source: Source = Field(default_factory=Source)
    data_mode: Literal["live", "test"] = "live"
    booking_url: str | None = None
    booking_status: Literal["matched", "mismatch", "unverified", "unavailable"] = "unverified"
    occupancy_status: Literal["matched", "mismatch", "unverified"] = "unverified"
    booking_context_issues: list[str] = Field(default_factory=list)
    occupancy_context_issues: list[str] = Field(default_factory=list)
    #: Booking-layer gate for the whole response. ``None`` means "not
    #: evaluated"; only ``ready=True`` may license an "可订" claim.
    bookability: BookingGate | None = None
    #: Id of the cheapest *qualified* quote (per room per night known cost).
    #: ``None`` means nothing qualified; the page must not fall back to
    #: "the first quote" or "the cheapest reference".
    qualified_lowest_id: str | None = None
    quotes: list[RoomQuote] = Field(default_factory=list)
    #: P7 decision, built only from evidence inside THIS response. Return-trip
    #: evidence from a separate travel request is never merged in here.
    decision: DecisionWire | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ApiError | None = None


class TravelPlaceRequest(Model):
    request_id: str = Field(min_length=1, max_length=64)
    provider: Literal["amap", "tencent", "didi"]
    city: str = Field(min_length=1, max_length=40)
    restaurant_keyword: str = Field(default="海底捞", min_length=1, max_length=30)
    destination_keyword: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("city", "restaurant_keyword", "destination_keyword")
    @classmethod
    def trim_value(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("required")
        return value.strip()


class PlaceOption(Model):
    id: str
    name: str
    address: str | None = None
    city: str | None = None
    name_match: bool | None = None
    address_match: bool | None = None
    city_match: bool | None = None


class MatchAssessment(Model):
    status: Literal["matched", "ambiguous", "unverified", "not_found"]
    selected_id: str | None = None
    explanation: str
    # How a matched verdict was reached, so the UI never calls a relaxed
    # comparison "strict". None means no match was reached.
    basis: Literal["exact", "address_core", "geocode"] | None = None


class TravelPlacesResponse(Model):
    request_id: str
    hotel_id: int
    hotel_name: str
    provider: Literal["amap", "tencent", "didi"]
    status: Literal["complete", "partial", "unavailable"]
    context_id: str | None = None
    queried_at: str
    expires_in_seconds: int = 120
    data_mode: Literal["live", "test"] = "live"
    hotels: list[PlaceOption] = Field(default_factory=list)
    restaurants: list[PlaceOption] = Field(default_factory=list)
    matching: MatchAssessment
    coverage_note: str
    warnings: list[str] = Field(default_factory=list)
    error: ApiError | None = None


class TravelRouteRequest(Model):
    request_id: str = Field(min_length=1, max_length=64)
    context_id: str = Field(min_length=1, max_length=64)
    hotel_option_id: str = Field(min_length=1, max_length=64)
    restaurant_option_id: str = Field(min_length=1, max_length=64)
    confirm_hotel: Annotated[bool, Field(strict=True)] = False


class WalkingEvidence(Model):
    origin_name: str
    destination_name: str
    distance_meters: int
    duration_seconds: int
    point_precision: Literal["poi_point"] = "poi_point"
    night_access_verified: Literal[False] = False


class DrivingEvidence(Model):
    origin_name: str
    destination_name: str
    distance_meters: int
    duration_seconds: int
    point_precision: Literal["poi_point"] = "poi_point"
    pickup_wait_verified: Literal[False] = False
    future_traffic_verified: Literal[False] = False


class SubwayEvidence(Model):
    name: str
    precision: Literal["named_entrance_poi", "station_poi"]
    walk: WalkingEvidence


class FareReference(Model):
    name: str
    lower_fen: Fen | None = None
    upper_fen: Fen | None = None
    capacity_verified: Literal[False] = False
    discount_eligibility_verified: Literal[False] = False


class TravelRouteResponse(Model):
    request_id: str
    hotel_id: int
    provider: Literal["amap", "tencent", "didi"]
    status: Literal["complete", "partial", "unavailable"]
    queried_at: str
    data_mode: Literal["live", "test"] = "live"
    hotel_name: str
    restaurant_name: str
    hotel_match_basis: Literal["exact_fields", "user_confirmed"]
    return_walk: WalkingEvidence | None = None
    return_drive: DrivingEvidence | None = None
    walk_limit_status: Literal["within_1500", "over_1500", "unverified"] = "unverified"
    drive_limit_status: Literal["within_900", "over_900", "unverified"] = "unverified"
    transport_status: Literal[
        "pass_walk_or_drive", "rejected_both_modes_over_limit", "unknown_or_partial"
    ] = "unknown_or_partial"
    subway: SubwayEvidence | None = None
    fares: list[FareReference] = Field(default_factory=list)
    current_reference_only: Literal[True] = True
    future_departure_verified: Literal[False] = False
    source_expiry_provided: Literal[False] = False
    opening_today: str | None = None
    opening_week: str | None = None
    #: P7 return-trip assessment, built only from this route's own evidence.
    #: Stay prices live in QuotesResponse and are never cross-joined here.
    return_trip: PlanAssessmentWire | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ApiError | None = None


class PlannedVisit(Model):
    """User-described destination; no map identity or coordinates verified."""
    name: str = Field(min_length=1, max_length=120)
    category: Literal["餐饮", "景点", "交通", "其他"]
    area_hint: str | None = Field(default=None, max_length=120)
    stay_near: bool = False


class ConditionDraft(Model):
    place: str | None = None
    place_type: str | None = None
    check_in: str | None = None
    check_out: str | None = None
    nights: int | None = None
    adults: int | None = None
    rooms: int | None = None
    children_count: int | None = None
    children_ages: list[int] | None = None
    budget_fen: int | None = None
    budget_basis: str | None = None
    notes: list[str] = Field(default_factory=list)
    visits: list[PlannedVisit] = Field(default_factory=list, max_length=5)


class ConversationState(Model):
    conversation_id: str
    revision: int
    generation: int
    client_sequence: int
    operation_id: str | None = None
    status: Literal[
        "idle",
        "queued",
        "running",
        "validating",
        "succeeded",
        "cancelled",
        "superseded",
        "failed",
        "timed_out",
    ]
    draft: ConditionDraft
    query: SearchInput | None = None
    missing: list[str]
    question: str
    warnings: list[str]
    error: ApiError | None = None
    provider: str | None = None
    model: str | None = None
    data_mode: Literal["live", "test"] = "live"


class InterpretRequest(Model):
    operation_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    client_sequence: Annotated[StrictInt, Field(ge=1, le=1000000)]
    user_inputs: list[str] = Field(min_length=1, max_length=6)
    provider: Literal["deepseek", "pi_chatgpt"] = "deepseek"

    @field_validator("user_inputs")
    @classmethod
    def validate_messages(cls, values):
        if any(not text.strip() or len(text) > 2000 for text in values):
            raise ValueError("invalid text")
        if sum(len(text.encode("utf-8")) for text in values) > 12000:
            raise ValueError("conversation input limit")
        return values


class CancelInterpretRequest(Model):
    operation_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")
    client_sequence: Annotated[StrictInt, Field(ge=1, le=1000000)]


class NamedHotel(Model):
    """User-supplied full operator identity, never an already-verified map or supplier POI."""

    name: str = Field(min_length=5, max_length=120)
    address: str = Field(min_length=8, max_length=240)

    @field_validator("name", "address")
    @classmethod
    def clean_fields(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or len(value.encode("utf-8")) > 300
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("invalid named hotel identity")
        return value


class RecallInput(Model):
    strategy: Literal["branch_place", "map_hotels", "named_hotel"] = "branch_place"
    operation_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")
    query: SearchInput
    provider: Literal["amap", "tencent"] = "amap"
    restaurant_keyword: str = Field(default="海底捞", min_length=1, max_length=30)
    branch_limit: Literal[3, 5] = 3
    named_hotel: NamedHotel | None = None
    restaurant_address: str | None = Field(default=None, min_length=6, max_length=240)

    @field_validator("restaurant_address")
    @classmethod
    def clean_restaurant_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("invalid restaurant address")
        return value

    @field_validator("restaurant_keyword")
    @classmethod
    def clean_keyword(cls, value):
        if not value.strip() or len(value.encode()) > 96:
            raise ValueError("invalid restaurant keyword")
        return value.strip()

    @model_validator(mode="after")
    def city_only(self):
        if (
            self.query.place_type != "城市"
            or len(self.query.place) > 40
            or any(c in self.query.place for c in "(),\r\n")
        ):
            raise ValueError("branch recall requires a city")
        if self.strategy == "named_hotel":
            if (
                self.named_hotel is None
                or not self.restaurant_address
                or len(self.named_hotel.name.encode("utf-8")) > 96
            ):
                raise ValueError("named hotel needs a full restaurant and hotel identity")
        elif self.named_hotel is not None or self.restaurant_address is not None:
            raise ValueError("named hotel identity not allowed for other strategies")
        return self


class RecallHotelTarget(Model):
    id: str
    name: str
    address: str
    city: str
    status: Literal["not_queried", "empty", "unavailable", "excluded", "ambiguous", "retained"] = (
        "not_queried"
    )
    reason: str | None = None
    source_count: int = 0
    hotel_id: int | None = None
    match_status: Literal["matched", "unverified"] | None = None


class RecallBranch(Model):
    role: Literal["restaurant_body", "auxiliary", "unknown"] = "unknown"
    role_reason: str = "insufficient_role_evidence"
    source_category: str | None = None
    nearby_status: Literal["not_requested", "complete", "unavailable"] = "not_requested"
    hotel_targets: list[RecallHotelTarget] = Field(default_factory=list)

    id: str
    name: str
    address: str | None = None
    city: str | None = None
    status: Literal["complete", "partial", "unavailable", "skipped"] = "skipped"
    query_place: str | None = None
    hotel_ids: list[int] = Field(default_factory=list)
    returned_count: int = 0
    limited: bool = False
    reason: str | None = None
    error: ApiError | None = None
    queried_at: str | None = None


class RecallObservation(Model):
    hotel_id: int
    queried_at: str


class RecallContinueRequest(Model):
    request_id: str = Field(min_length=1, max_length=64)
    continuation_cursor: str = Field(pattern=r"^[a-f0-9]{32}$")
    operation_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")


class RecallCoverage(Model):
    total_hotel_lookups: int = 0
    total_quote_http_requests: int = 0

    map_http_requests: int = 0
    nearby_branches_attempted: int = 0
    named_hotel_map_lookups: int = 0
    named_hotel_map_matches: int = 0
    nearby_invalid: int = 0
    hotel_lookups_attempted: int = 0
    hotel_lookup_limit: int = 5
    lookup_reuses: int = 0
    excluded_source_hotels: int = 0
    unqueried_targets: int = 0

    role_restaurant_bodies: int = 0
    role_auxiliary: int = 0
    role_unknown: int = 0
    pages_requested: int = 0
    raw_places: int = 0
    unique_places: int = 0
    invalid_places: int = 0
    total_hint: int | None = None
    discovery_stop_reason: str
    discovery_complete: bool = False
    queried_branches: int = 0
    branch_limit: int
    identity_conflicts: int = 0
    duplicate_hotels: int = 0
    identical_branch_sets: int = 0
    quote_http_requests: int = 0


class RecallResponse(SearchResponse):
    attempted_hotel_names: list[str] = Field(default_factory=list)
    continuation_cursor: str | None = None
    continuation_expires_at: str | None = None
    plan_queried_at: str | None = None
    revision: int = 0
    round_number: int = 1
    continuation_delta: bool = False
    removed_hotel_ids: list[int] = Field(default_factory=list)
    retained_total: int = 0
    hotel_observations: list[RecallObservation] = Field(default_factory=list)

    strategy: Literal["branch_place", "map_hotels", "named_hotel"] = "branch_place"
    walk_check_available: bool = False
    operation_id: str
    map_provider: Literal["amap", "tencent"]
    restaurant_keyword: str
    branches: list[RecallBranch] = Field(default_factory=list)
    recall_coverage: RecallCoverage


class BranchWalkBatchRequest(Model):
    request_id: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")
    offset: Annotated[StrictInt, Field(ge=0, le=99)] = 0


class BranchWalkRouteRequest(Model):
    request_id: str = Field(min_length=1, max_length=64)
    context_id: str = Field(min_length=1, max_length=64)
    hotel_option_id: str = Field(min_length=1, max_length=64)
    restaurant_option_id: str = Field(min_length=1, max_length=64)
    confirm_hotel: Annotated[bool, Field(strict=True)] = False
    confirm_restaurant: Annotated[bool, Field(strict=True)] = False


class BranchWalkPair(Model):
    recall_hotel: PlaceOption | None = None
    source_http_requests: int = 0
    context_id: str | None = None
    branch_id: str
    hotel_id: int
    original_hotel_name: str
    original_hotel_address: str | None = None
    original_restaurant: PlaceOption
    status: Literal["needs_confirmation", "route_verified", "unavailable", "skipped"]
    hotels: list[PlaceOption] = Field(default_factory=list)
    restaurants: list[PlaceOption] = Field(default_factory=list)
    hotel_matching: MatchAssessment
    restaurant_matching: MatchAssessment
    walk: WalkingEvidence | None = None
    walk_limit_status: Literal["within_1500", "over_1500", "unverified"] = "unverified"
    drive: DrivingEvidence | None = None
    drive_limit_status: Literal["within_900", "over_900", "unverified"] = "unverified"
    transport_status: Literal[
        "pass_walk_or_drive", "rejected_both_modes_over_limit", "unknown_or_partial"
    ] = "unknown_or_partial"
    hotel_match_basis: Literal["exact_fields", "user_confirmed"] | None = None
    restaurant_match_basis: Literal["exact_fields", "user_confirmed"] | None = None
    queried_at: str
    expires_in_seconds: int = 120
    error: ApiError | None = None
    data_mode: Literal["live", "test"] = "live"


class BranchWalkBatchResponse(Model):
    request_id: str
    operation_id: str
    provider: Literal["amap", "tencent"]
    status: Literal["complete", "partial", "unavailable"]
    offset: int
    total_pairs: int
    next_offset: int | None = None
    processed_pairs: int
    map_http_requests: int
    pairs: list[BranchWalkPair] = Field(default_factory=list)
    queried_at: str
    warnings: list[str]
    error: ApiError | None = None
    data_mode: Literal["live", "test"] = "live"


# R102: explicit, source-bound map discovery; no guessed location, booking or rating.
class NearbyResolveRequest(Model):
    provider: Literal["amap", "tencent"] = "amap"
    keyword: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=40)
    country_region: str | None = Field(default=None, max_length=80)

    @field_validator("keyword", "city", "country_region")
    @classmethod
    def safe_location_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid location text")
        return value


class NearbyPlaceOption(Model):
    id: str
    name: str
    address: str | None = None
    city: str | None = None
    category: str | None = None


class NearbyResolveResponse(Model):
    provider: Literal["amap", "tencent"]
    context_id: str | None = None
    queried_at: str
    data_mode: Literal["live", "test"]
    status: Literal["complete", "partial", "unavailable"]
    options: list[NearbyPlaceOption] = Field(default_factory=list)
    expires_in_seconds: int = 0
    coverage_note: str
    error: ApiError | None = None


class NearbyExploreRequest(Model):
    context_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    anchor_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_id: str | None = Field(default=None, min_length=1, max_length=64)


class NearbyGuestReview(Model):
    valid_until: str
    score: float = Field(gt=0,le=5)
    scale: Literal[5] = 5
    review_count: StrictInt = Field(ge=1,le=100000000)
    source: str = Field(min_length=1,max_length=64)
    observed_at: str
    authorization_status: Literal["operator_attested"] = "operator_attested"


class NearbyHotelOption(Model):
    id: str
    name: str
    address: str
    city: str
    straight_line_meters: int = Field(ge=0, le=2000)
    source_hotel_id: int | None = None
    source_match: Literal["matched", "unverified", "ambiguous"] = "unverified"
    walking_distance_meters: int | None = None
    walking_duration_seconds: int | None = None
    rating_status: Literal["unavailable", "map_poi"] = "unavailable"
    # Same-map Amap business.rating (0-5); NEVER label as a supplier guest score.
    map_rating: float | None = Field(default=None, ge=0, le=5)
    # Unknown until an explicitly authorized hotel-review provider proves them.
    review_count: int | None = Field(default=None, ge=0)
    stayed_count: int | None = Field(default=None, ge=0)
    guest_review: NearbyGuestReview | None = None


class NearbyExploreResponse(Model):
    context_id: str
    anchor_id: str
    provider: Literal["amap", "tencent"]
    queried_at: str
    data_mode: Literal["live", "test"]
    status: Literal["complete", "partial", "unavailable"]
    hotels: list[NearbyHotelOption] = Field(default_factory=list)
    coverage_note: str
    ranking_status: Literal["not_comparable"] = "not_comparable"
    ranking_reason: str
    error: ApiError | None = None


class ParallelNearbyExploreRequest(NearbyExploreRequest):
    operation_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")


class NearbyRecommendationLink(Model):
    request_id: str
    hotel_id: int
    hotel_option_id: str


class ParallelNearbyExploreResponse(NearbyExploreResponse):
    recommendation_links: list[NearbyRecommendationLink] = Field(default_factory=list)


class NearbyWalkRequest(Model):
    context_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    hotel_option_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class NearbyWalkResponse(Model):
    context_id: str
    hotel_option_id: str
    provider: Literal["amap", "tencent"]
    queried_at: str
    data_mode: Literal["live", "test"]
    status: Literal["complete", "unavailable"]
    distance_meters: int | None = None
    duration_seconds: int | None = None
    error: ApiError | None = None


class NearbyIdentityStatusRequest(Model):
    context_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    anchor_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_id: str = Field(min_length=1, max_length=64, strict=True)


class NearbyIdentityOperation(NearbyIdentityStatusRequest):
    hotel_option_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_hotel_id: StrictInt = Field(gt=0, lt=2**31)
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    intent_seq: StrictInt = Field(ge=1)
    expected_revision: StrictInt = Field(ge=0)


class NearbyIdentityConfirmRequest(NearbyIdentityOperation):
    confirmed: bool = Field(strict=True)

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit confirmation required")
        return value


class NearbyIdentityRevokeRequest(NearbyIdentityOperation):
    target_operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class NearbyIdentityPair(Model):
    hotel_option_id: str
    source_hotel_id: int
    map_name: str
    map_address: str
    map_city: str
    source_name: str
    source_address: str
    source_city_status: Literal["missing"]
    source_observed_at: str


class NearbyIdentityBinding(Model):
    hotel_option_id: str
    source_hotel_id: int
    operation_id: str
    identity_basis: Literal["user_confirmed"]


class NearbyIdentityResponse(Model):
    context_id: str
    anchor_id: str
    request_id: str
    status: Literal["complete", "unavailable"]
    revision: int = Field(ge=0)
    intent_seq: int = Field(default=0, ge=0)
    expires_in_seconds: float = Field(ge=0, le=120)
    bindings: list[NearbyIdentityBinding] = Field(default_factory=list)
    eligible_pairs: list[NearbyIdentityPair] = Field(default_factory=list)
    reason: str
    error: ApiError | None = None
