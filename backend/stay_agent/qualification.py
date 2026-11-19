"""P5 evidence semantics: what a source may actually prove about a stay.

Pure and network-free. Every helper answers an *evidence* question, never a
booking question, and no helper upgrades a supplier value into a guarantee.

Approved assumptions from the user on 2026-11-05 are applied here as
**presumptions**, never as ``verified``:

1. A whole overnight room type has a private bathroom unless the room type says
   otherwise (shared bathroom, dormitory or bed place). A room type whose name
   merely says "独卫" is still only *consistent* with the default; it is not
   positive field evidence, and hotel-level amenities never take part.
2. A domestic display price already includes tax and service fees, so no
   separate tax line is added.
3. The requested occupancy combination may be presumed from request parameters
   plus the source remaining count plus the snapshot time, because no source
   echoes adults/rooms back.
4. An averaged price may be multiplied out into a *presumed* stay total, even
   when the unit is unstated, and must then carry the averaged-price label.

The rule layer (P6) may treat a presumption as satisfied, but the page must
label it as a presumption and must never call it confirmed or bookable. Two
gate layers exist: ``reference`` (display a reference-only card) and
``booking`` (claim a bookable, all-fees total). Only the first can pass today.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
import re
import unicodedata

Status = Literal["verified", "presumed", "unknown", "unsupported", "blocked"]
UseStatus = Literal["permitted", "prohibited", "out_of_scope", "unstated"]

# --- field identifiers -------------------------------------------------------

ROOM_EXCLUSIVITY = "room_exclusivity"
PRIVATE_BATHROOM = "private_bathroom"
INVENTORY_QUANTITY = "inventory_quantity"
INSTANT_BOOKING = "instant_booking"
PRICE_INCLUDES_TAX = "price_includes_tax"
PRICE_UNIT = "price_unit"
NIGHTLY_BREAKDOWN = "nightly_breakdown"
DEPOSIT_AND_FEES = "deposit_and_mandatory_fees"
OCCUPANCY_ECHO = "occupancy_echo"
BOOKING_LINK = "safe_booking_link"
CANCELLATION = "cancellation_rule"
GUEST_SCORE = "guest_score"

#: Fields a full decision needs before any quote can be called qualified.
REQUIRED_FIELDS: tuple[str, ...] = (
    ROOM_EXCLUSIVITY,
    PRIVATE_BATHROOM,
    INVENTORY_QUANTITY,
    PRICE_INCLUDES_TAX,
    PRICE_UNIT,
    NIGHTLY_BREAKDOWN,
    DEPOSIT_AND_FEES,
    OCCUPANCY_ECHO,
    BOOKING_LINK,
)

#: Unknown-but-user-approved defaults. The value stays unknown; the rule layer
#: may still proceed and must show the assumption.
USER_APPROVED_DEFAULTS: frozenset[str] = frozenset(
    {ROOM_EXCLUSIVITY, PRIVATE_BATHROOM, PRICE_INCLUDES_TAX}
)

# --- room form and bathroom -------------------------------------------------

#: Bed place / dormitory wording: not a whole overnight room.
DORMITORY_MARKERS: tuple[str, ...] = (
    "床位", "上下铺", "通铺", "多人间", "宿舍", "青旅", "合住",
)

#: Wording that explicitly denies a private bathroom in the room type.
SHARED_BATHROOM_MARKERS: tuple[str, ...] = (
    "公共卫浴", "公共浴室", "公共卫生间", "公卫",
    "共用卫生间", "共用卫浴", "共用浴室", "公用卫生间",
    "无独立卫浴", "无独卫", "不含独立卫浴",
)


def _normalise(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    return "".join(text.split())


def _matches(text: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        if marker in text:
            return marker
    return None


@dataclass(frozen=True)
class Assumption:
    """An evidence judgement that is satisfied only by an approved default."""

    satisfied: bool
    basis: str
    label: str
    #: A default never counts as field-verified evidence.
    verified: bool = False


def room_form(room_name: str | None) -> Assumption:
    """Whole overnight room unless the room type names a dormitory/bed place."""
    if type(room_name) is not str or not room_name.strip():
        return Assumption(
            False, "room_name_missing",
            "房型名称缺失，不能套用整间默认推定。",
        )
    marker = _matches(_normalise(room_name), DORMITORY_MARKERS)
    if marker is not None:
        return Assumption(
            False, f"explicit_shared_marker:{marker}",
            f"房型标注“{marker}”，不是整间过夜客房。",
        )
    return Assumption(
        True, "domestic_default_whole_room",
        "按整间过夜客房处理（未见床位或多人间标记）。",
    )


def private_bathroom(room_name: str | None) -> Assumption:
    """Bathroom default for a whole room; shared wording excludes it.

    Hotel-level amenities and a room name that merely says "独卫" are not
    evidence; only an explicit shared/dormitory marker changes the outcome.
    """
    if type(room_name) is not str or not room_name.strip():
        return Assumption(
            False, "room_name_missing",
            "房型名称缺失，不能套用房内独卫默认推定。",
        )
    text = _normalise(room_name)
    bath = _matches(text, SHARED_BATHROOM_MARKERS)
    if bath is not None:
        return Assumption(
            False, f"explicit_shared_marker:{bath}",
            f"房型标注“{bath}”，不按房内独卫处理。",
        )
    dorm = _matches(text, DORMITORY_MARKERS)
    if dorm is not None:
        return Assumption(
            False, f"dormitory_marker:{dorm}",
            f"房型标注“{dorm}”，不作房内独卫推定。",
        )
    return Assumption(
        True, "domestic_default_private_bathroom",
        "未取得来源独卫字段；按国内默认推定为房内独卫（未见公共卫浴或床位标记）。",
    )


# --- inventory --------------------------------------------------------------

InventoryStatus = Literal["sufficient", "insufficient", "instant_unquantified", "unknown"]


@dataclass(frozen=True)
class InventoryAssessment:
    status: InventoryStatus
    basis: str
    detail: str

    @property
    def meets(self) -> bool:
        """Only a per-stay remaining count can satisfy the hard condition."""
        return self.status == "sufficient"


def inventory_from_remaining(remaining: int | None, rooms: int) -> InventoryAssessment:
    """Tuniu ``count``: official remaining stock for the queried stay."""
    if type(rooms) is not int or rooms < 1:
        raise ValueError("rooms must be a positive integer")
    if type(remaining) is not int or remaining < 0:
        return InventoryAssessment(
            "unknown", "remaining_count_missing_or_invalid",
            "来源未返回可核实的剩余房量。",
        )
    if remaining >= rooms:
        return InventoryAssessment(
            "sufficient", "official_remaining_stock_count",
            f"来源剩余 {remaining} 间 ≥ 本次 {rooms} 间；仅代表该次查询日期的快照。",
        )
    return InventoryAssessment(
        "insufficient", "official_remaining_stock_count",
        f"来源剩余 {remaining} 间 < 本次 {rooms} 间，库存不足。",
    )


def inventory_from_instant_booking(
    is_on_request: bool | None, rooms: int,
) -> InventoryAssessment:
    """RollingGo ``isOnRequest``: instant booking flag, never a room count.

    The official definition is "是否需延迟人工放盘审批": ``false`` means the
    rate plan can be booked instantly, ``true`` means a manual release is
    required. Neither value states how many rooms exist, so this can never
    satisfy the quantity condition on its own.
    """
    if type(rooms) is not int or rooms < 1:
        raise ValueError("rooms must be a positive integer")
    if is_on_request is False:
        return InventoryAssessment(
            "instant_unquantified", "official_instant_booking_flag",
            "来源标记为立订（无需人工放盘审批），但协议没有剩余房量字段，"
            f"不能证明可同时提供 {rooms} 间。",
        )
    if is_on_request is True:
        return InventoryAssessment(
            "unknown", "official_manual_release_required",
            "来源标记为需人工放盘确认，当前不可视为可订。",
        )
    return InventoryAssessment(
        "unknown", "instant_booking_flag_missing",
        "来源未返回立订标记，库存状态未知。",
    )


# --- money ------------------------------------------------------------------

@dataclass(frozen=True)
class PriceSemantics:
    per_room_night: bool
    tax_included: bool | None
    basis: str


PRICE_SEMANTICS: dict[str, PriceSemantics] = {
    # Official: averagePrice = "针对该时段均摊每一间夜的折后总含税均价".
    "rollinggo": PriceSemantics(True, True, "official_per_room_night_tax_inclusive"),
    # Official: rmbPrices = "价格（元）"; unit and tax treatment are not stated.
    "tuniu": PriceSemantics(False, None, "official_price_in_yuan_unit_unstated"),
    # Official sample omits currency; the adapter drops an ambiguous price.
    "flyai": PriceSemantics(False, None, "published_sample_omits_currency"),
}


def stay_total_fen(
    amount_fen: int | None, nights: int, rooms: int, *, source_id: str,
) -> int | None:
    """Multiply only when the source states a per-room-night amount.

    A per-stay total is never invented from an amount whose unit is unstated.
    Even for a stated per-night average, the result is an averaged total, not
    a night-by-night quote.
    """
    if type(nights) is not int or nights < 1 or type(rooms) is not int or rooms < 1:
        raise ValueError("nights and rooms must be positive integers")
    if type(amount_fen) is not int or amount_fen < 0:
        return None
    semantics = PRICE_SEMANTICS.get(source_id)
    if semantics is None or not semantics.per_room_night:
        return None
    return amount_fen * nights * rooms


# --- approved presumptions (user 2026-11-05) ---------------------------------

#: Snapshot format accepted as "when the source was observed": a date is the
#: minimum, a full ISO timestamp with offset is preferred.
#:
#: Fractional seconds are allowed on purpose: the adapters emit
#: ``datetime.now(timezone.utc).isoformat()``, which carries microseconds
#: (``...T16:33:50.123456+00:00``). r66 real-data run showed that rejecting
#: them made every Tuniu quote fail the occupancy presumption, so the reference
#: layer could never pass on live data while unit tests (second-precision
#: fixtures) stayed green.
_SNAPSHOT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?([+-]\d{2}:?\d{2}|Z)?)?$"
)


def auditable_snapshot(value: object) -> bool:
    """Shape *and* a real calendar check for a source observation time.

    A regex alone accepts impossible dates (``2026-13-07``); the presumption
    this guards claims the observation time is auditable, so the value must
    also parse as ISO 8601.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not _SNAPSHOT_RE.match(text):
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Presumption:
    """A condition satisfied only by an approved presumption."""

    satisfied: bool
    basis: str
    label: str


def occupancy_combo(
    *,
    adults: int | None,
    rooms: int | None,
    remaining: int | None,
    snapshot_at: str | None,
) -> Presumption:
    """Presume the stay combination from request params, count and snapshot.

    Approved 2026-11-05: no source echoes the requested adults/rooms back, so
    the combination is shown as presumed, never as source-confirmed. All three
    inputs are required; a missing snapshot makes the presumption unauditable.
    """
    if type(adults) is not int or adults < 1 or type(rooms) is not int or rooms < 1:
        return Presumption(
            False, "request_params_missing",
            "缺少本次请求的成人数或房数，不能推定入住组合。",
        )
    if type(remaining) is not int or remaining < 0:
        return Presumption(
            False, "remaining_count_missing",
            "来源未返回剩余房量，不能推定本次入住组合。",
        )
    if not auditable_snapshot(snapshot_at):
        return Presumption(
            False, "snapshot_time_missing",
            "缺少来源观察时间，不能推定本次入住组合。",
        )
    return Presumption(
        True, "request_params_plus_remaining_count_plus_snapshot",
        f"入住组合未回显：按本次请求（{adults} 成人／{rooms} 房）＋来源剩余 "
        f"{remaining} 间＋观察时间 {snapshot_at.strip()} 推定，非来源确认，"
        "不是可订承诺。",
    )


@dataclass(frozen=True)
class PresumedTotal:
    """Averaged amount folded into a stay total, with the label it requires."""

    amount_fen: int | None
    basis: str
    label: str
    #: True only when the source itself states a per-room-night amount.
    official_unit: bool


def presumed_stay_total_fen(
    amount_fen: int | None, nights: int, rooms: int, *, source_id: str,
) -> PresumedTotal:
    """Fold an averaged amount into a *presumed* stay total.

    Approved 2026-11-05 for Tuniu, whose unit is unstated. The result is always
    labelled: it is neither a night-by-night quote nor a bookable total, and if
    the supplier meant a whole-stay amount this figure is an overestimate.
    """
    if type(nights) is not int or nights < 1 or type(rooms) is not int or rooms < 1:
        raise ValueError("nights and rooms must be positive integers")
    if type(amount_fen) is not int or amount_fen < 0:
        return PresumedTotal(None, "amount_missing", "来源未返回可用金额。", False)
    if source_id == "flyai":
        return PresumedTotal(
            None, "currency_unconfirmed",
            "公开样例缺币种，不得折算整住期金额。", False,
        )
    total = amount_fen * nights * rooms
    if source_id == "rollinggo":
        return PresumedTotal(
            total, "official_per_room_night_average",
            f"官方每间夜含税均价折算计：{nights} 晚 × {rooms} 间＝推定总额，"
            "并非逐夜实价。", True,
        )
    return PresumedTotal(
        total, "user_approved_average_times_nights",
        f"来源未注明计价单位，按均价×{nights} 晚×{rooms} 间推定整住期金额；"
        "若来源为整单价，此值会偏高；非逐夜实价、非可订实付价。", False,
    )


def nightly_breakdown_presumption(source_id: str, nights: int) -> Presumption:
    """Multi-night stays only ever get an averaged, not a nightly, breakdown."""
    if type(nights) is not int or nights < 1:
        raise ValueError("nights must be a positive integer")
    if source_id not in FIELD_STATUS:
        raise ValueError(f"unknown source: {source_id}")
    if nights == 1:
        return Presumption(True, "not_required_single_night", "单晚无需逐夜拆分。")
    if FIELD_STATUS[source_id][NIGHTLY_BREAKDOWN][0] == "verified":
        return Presumption(True, "official_nightly_breakdown", "来源逐夜价。")
    if source_id == "flyai":
        return Presumption(
            False, "search_only_no_nightly_detail",
            "候选发现无逐夜价，不能推定多晚金额。",
        )
    return Presumption(
        True, "averaged_price_no_nightly_detail",
        f"只有均价、无逐夜差异：按均价折算 {nights} 晚，无法排除其中一晚超预算。",
    )


# --- source field matrix ----------------------------------------------------

FIELD_STATUS: dict[str, dict[str, tuple[Status, str]]] = {
    "rollinggo": {
        ROOM_EXCLUSIVITY: ("unknown", "official_room_info_has_no_exclusivity_field"),
        PRIVATE_BATHROOM: ("unknown", "official_room_info_has_no_bathroom_field"),
        INVENTORY_QUANTITY: ("unsupported", "official_detail_has_no_room_count_field"),
        INSTANT_BOOKING: ("verified", "official_false_means_instant_booking"),
        PRICE_INCLUDES_TAX: ("verified", "official_discounted_tax_inclusive_average"),
        PRICE_UNIT: ("verified", "official_per_room_night_average"),
        NIGHTLY_BREAKDOWN: ("unsupported", "official_detail_returns_averaged_price"),
        DEPOSIT_AND_FEES: ("unknown", "hotel_level_policy_not_rate_plan_level"),
        OCCUPANCY_ECHO: ("unsupported", "official_detail_has_no_occupancy_echo"),
        BOOKING_LINK: ("verified", "r56_live_matched_verbatim_url"),
        CANCELLATION: ("verified", "official_cancel_policy_per_rate_plan"),
        GUEST_SCORE: ("unknown", "scale_unstated_when_absent"),
    },
    "tuniu": {
        ROOM_EXCLUSIVITY: ("unknown", "official_room_types_has_no_exclusivity_field"),
        PRIVATE_BATHROOM: ("unknown", "official_room_types_has_no_bathroom_field"),
        INVENTORY_QUANTITY: ("verified", "official_count_is_remaining_stock"),
        INSTANT_BOOKING: ("unsupported", "official_detail_has_no_instant_flag"),
        PRICE_INCLUDES_TAX: ("unknown", "official_price_in_yuan_tax_unstated"),
        PRICE_UNIT: ("unknown", "official_price_in_yuan_unit_unstated"),
        NIGHTLY_BREAKDOWN: ("unsupported", "official_detail_returns_single_price"),
        DEPOSIT_AND_FEES: (
            "unsupported",
            "r67_live_detail_policies_has_only_cancel_and_checkin_keys"
            "_no_deposit_field_in_the_two_allowed_read_tools",
        ),
        OCCUPANCY_ECHO: (
            "unsupported",
            "official_detail_has_no_occupancy_echo_r59_live_2026_11_05_confirmed",
        ),
        BOOKING_LINK: ("unsupported", "official_detail_returns_no_booking_url"),
        CANCELLATION: ("verified", "official_cancel_desc_per_rate_plan"),
        GUEST_SCORE: ("unknown", "official_score_scale_not_stated"),
    },
    "flyai": {
        ROOM_EXCLUSIVITY: ("unknown", "discovery_hint_has_no_room_type"),
        PRIVATE_BATHROOM: ("unknown", "discovery_hint_has_no_room_type"),
        INVENTORY_QUANTITY: ("unsupported", "search_only_no_stock_field"),
        INSTANT_BOOKING: ("unsupported", "search_only_no_instant_flag"),
        PRICE_INCLUDES_TAX: ("unknown", "published_sample_omits_currency"),
        PRICE_UNIT: ("unknown", "published_sample_omits_currency"),
        NIGHTLY_BREAKDOWN: ("unsupported", "search_only_no_nightly_detail"),
        DEPOSIT_AND_FEES: ("unsupported", "search_only_no_fee_detail"),
        OCCUPANCY_ECHO: ("unsupported", "search_only_no_occupancy_echo"),
        BOOKING_LINK: ("unsupported", "hint_has_no_booking_url"),
        CANCELLATION: ("unsupported", "search_only_no_cancel_rule"),
        GUEST_SCORE: ("unknown", "scale_unstated"),
    },
}


def field_status(source_id: str, field: str) -> Status | None:
    """Declared state of one field, or ``None`` when the source is unknown.

    Projections read this instead of hardcoding "this source states tax
    included": the table above is the single place that knowledge lives, so a
    corrected status cannot leave a stale ``True`` behind in a service.
    """
    row = FIELD_STATUS.get(source_id, {}).get(field)
    return row[0] if row else None


@dataclass(frozen=True)
class GateResult:
    source_id: str
    nights: int
    verified: tuple[str, ...]
    defaulted: tuple[str, ...]
    blocked: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blocked


def gate_status(source_id: str, *, nights: int = 1, rooms: int = 1) -> GateResult:
    """Per-source readiness for a full decision.

    A single-night stay needs neither a nightly breakdown nor a verified price
    unit, because a one-night amount is the same whether quoted per night or
    per stay. Multi-night stays keep both requirements.
    """
    if type(nights) is not int or nights < 1 or type(rooms) is not int or rooms < 1:
        raise ValueError("nights and rooms must be positive integers")
    if source_id not in FIELD_STATUS:
        raise ValueError(f"unknown source: {source_id}")
    statuses = FIELD_STATUS[source_id]
    verified: list[str] = []
    defaulted: list[str] = []
    blocked: list[str] = []
    for field in REQUIRED_FIELDS:
        if nights == 1 and field in (PRICE_UNIT, NIGHTLY_BREAKDOWN):
            continue
        status = statuses[field][0]
        if status == "verified":
            verified.append(field)
        elif status == "unknown" and field in USER_APPROVED_DEFAULTS:
            defaulted.append(field)
        else:
            blocked.append(field)
    return GateResult(
        source_id, nights, tuple(verified), tuple(defaulted), tuple(blocked),
    )


# --- two-layer gate ---------------------------------------------------------

REFERENCE_LAYER = "reference"
BOOKING_LAYER = "booking"

#: Sources whose domestic display price may be treated as tax-inclusive
#: (user 2026-11-05). RollingGo is verified, not presumed, so it is absent.
PRESUMED_TAX_INCLUDED: frozenset[str] = frozenset({"tuniu"})

#: Sources whose averaged amount may be folded into a presumed stay total.
PRESUMED_TOTAL_SOURCES: frozenset[str] = frozenset({"rollinggo", "tuniu"})


@dataclass(frozen=True)
class LayerGate:
    """Readiness for one layer, with every presumption and caveat spelled out."""

    source_id: str
    layer: str
    nights: int
    rooms: int
    verified: tuple[str, ...]
    presumed: tuple[str, ...]
    blocked: tuple[str, ...]
    caveats: tuple[str, ...]
    #: Page text required for each presumption and caveat; never optional.
    labels: tuple[str, ...]
    #: Field -> the sentence actually produced for this verdict. Presentation
    #: prefers this over the static table: a static sentence would describe a
    #: different source's field state (e.g. "supplier reports remaining stock"
    #: for a source that has no stock field at all).
    notes: tuple[tuple[str, str], ...] = ()
    #: Fields that would block, except the user explicitly accepted the trade-off
    #: for this query (today only: accepting a bed place relaxes the whole-room
    #: and private-bathroom requirements). Accepted is neither verified nor
    #: presumed: the source still says nothing, we did not guess, the user chose.
    accepted: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blocked


def reference_gate(
    source_id: str,
    *,
    nights: int = 1,
    rooms: int = 1,
    adults: int = 2,
    remaining: int | None = None,
    snapshot_at: str | None = None,
    room_name: str | None = None,
    accepts_dormitory: bool = False,
) -> LayerGate:
    """Can this source support a reference-only card for this stay?

    Reference layer = display an unbooked, clearly-labelled reference price.
    Deposit/incidental fees and a verbatim link are *caveats* here (they only
    block the booking layer), because the card claims neither an all-fees total
    nor a jump. Inventory, room form and occupancy stay blocking.

    ``accepts_dormitory`` only relaxes whole-room and private-bathroom, and only
    for a room name that actually says it is a bed place / dormitory: the user's
    answer to "is a bed acceptable" must not silently widen an ordinary room.
    The relaxed rows are reported as ``accepted``, never as verified or presumed.
    """
    if type(nights) is not int or nights < 1 or type(rooms) is not int or rooms < 1:
        raise ValueError("nights and rooms must be positive integers")
    if source_id not in FIELD_STATUS:
        raise ValueError(f"unknown source: {source_id}")
    statuses = FIELD_STATUS[source_id]
    verified: list[str] = []
    presumed: list[str] = []
    blocked: list[str] = []
    caveats: list[str] = []
    accepted: list[str] = []
    labels: list[str] = []
    notes: dict[str, str] = {}

    def note(field: str, text: str) -> None:
        labels.append(text)
        notes[field] = text

    form = room_form(room_name)
    bath = private_bathroom(room_name)
    bed_marker = (
        _matches(_normalise(room_name), DORMITORY_MARKERS)
        if accepts_dormitory and type(room_name) is str and room_name.strip()
        else None
    )
    combo = occupancy_combo(
        adults=adults, rooms=rooms, remaining=remaining, snapshot_at=snapshot_at,
    )
    nightly = nightly_breakdown_presumption(source_id, nights)

    for field in REQUIRED_FIELDS:
        if nights == 1 and field in (PRICE_UNIT, NIGHTLY_BREAKDOWN):
            continue
        status = statuses[field][0]
        if field in (ROOM_EXCLUSIVITY, PRIVATE_BATHROOM):
            assumption = form if field == ROOM_EXCLUSIVITY else bath
            if assumption.satisfied:
                presumed.append(field)
                note(field, assumption.label)
            elif bed_marker is not None:
                accepted.append(field)
                note(
                    field,
                    f"房型标注“{bed_marker}”；你已明确接受床位，本项不作为阻断"
                    "（接受床位同时视为接受这类房型常见的共享卫浴）。",
                )
            else:
                blocked.append(field)
                note(field, assumption.label)
        elif field == INVENTORY_QUANTITY:
            if status == "verified":
                inventory = inventory_from_remaining(remaining, rooms)
                (verified if inventory.meets else blocked).append(field)
                note(field, inventory.detail)
            elif status == "unsupported":
                blocked.append(field)
                note(field, 
                    "来源协议没有剩余房量字段，不能作为有房依据。"
                )
            else:
                blocked.append(field)
                note(field, f"库存字段状态为 {status}，不放行。")
        elif field == OCCUPANCY_ECHO:
            if status == "verified":
                verified.append(field)
            elif combo.satisfied:
                presumed.append(field)
                note(field, combo.label)
            else:
                blocked.append(field)
                note(field, combo.label)
        elif field in (PRICE_INCLUDES_TAX, PRICE_UNIT):
            if status == "verified":
                verified.append(field)
            elif field == PRICE_INCLUDES_TAX and source_id in PRESUMED_TAX_INCLUDED:
                presumed.append(field)
                note(field, "境内展示价按已含税费与服务费处理（用户 2026-11-05 确认）。")
            elif field == PRICE_UNIT and source_id in PRESUMED_TOTAL_SOURCES:
                presumed.append(field)
                note(field, 
                    "来源未注明计价单位，按均价×晚数折算整住期金额，标“推定”。"
                )
            else:
                blocked.append(field)
                note(field, f"计价字段状态为 {status}，不放行。")
        elif field == NIGHTLY_BREAKDOWN:
            if status == "verified":
                verified.append(field)
            elif nightly.satisfied:
                presumed.append(field)
                note(field, nightly.label)
            else:
                blocked.append(field)
                note(field, nightly.label)
        elif field in (DEPOSIT_AND_FEES, BOOKING_LINK):
            if status == "verified":
                verified.append(field)
            else:
                caveats.append(field)
                note(field, 
                    "到店押金／附加费未核实，不并入参考价，页面须标注“到店另付未核实”。"
                    if field == DEPOSIT_AND_FEES
                    else "无来源原样链接，卡片不提供跳转，需自行前往来源核对。"
                )
        else:
            (verified if status == "verified" else blocked).append(field)
    return LayerGate(
        source_id, REFERENCE_LAYER, nights, rooms,
        tuple(verified), tuple(presumed), tuple(blocked), tuple(caveats),
        tuple(labels), tuple(notes.items()), tuple(accepted),
    )


def booking_gate(
    source_id: str, *, nights: int = 1, rooms: int = 1,
    link_matched: bool | None = None,
    occupancy_echo_matched: bool | None = None,
) -> LayerGate:
    """Can this source support a bookable, all-fees total? No source can yet.

    Booking layer accepts verified fields only: occupancy echo, deposit and a
    verbatim link are never presumed here. The static per-source table answers
    "has this source ever provided the field"; the two runtime flags answer
    "did *this* response pass the context check". A runtime failure always
    demotes a statically verified field, never the other way round.
    """
    strict = gate_status(source_id, nights=nights, rooms=rooms)
    verified = list(strict.verified)
    blocked = list(strict.blocked)
    notes = {
        field: "可订层只接受已核实字段；该项未核实或来源不提供，因此阻断。"
        for field in blocked
    }
    runtime = (
        (BOOKING_LINK, link_matched, "本次来源链接未通过参数核对，可订层不接受该链接。"),
        (OCCUPANCY_ECHO, occupancy_echo_matched,
         "本次来源未回显一致的人数与房数组合，可订层不接受推定。"),
    )
    for field, passed, reason in runtime:
        if passed is not False:
            continue
        if field in verified:
            verified.remove(field)
        if field not in blocked:
            blocked.append(field)
        # A runtime reason is more specific than the static one, so it wins
        # even when the table already blocked the field.
        notes[field] = reason
    return LayerGate(
        source_id, BOOKING_LAYER, nights, rooms,
        tuple(verified), (), tuple(blocked), (),
        ("可订层不接受推定：入住组合回显、押金／到店必付、来源链接须逐项核实。",),
        tuple(notes.items()),
    )


# --- permitted use ----------------------------------------------------------

USE_STATUS: dict[str, tuple[UseStatus, str]] = {
    "display_local_personal": (
        "permitted",
        "user_scope_2026_11_05_personal_non_commercial",
    ),
    "cache_raw_response": (
        "prohibited",
        "no_written_licence_and_spec_minimisation",
    ),
    "cross_source_price_merge": (
        "prohibited",
        "spec_r05_r09_no_cross_source_entity_or_price_merge",
    ),
    "public_deployment_or_commercial_use": (
        "out_of_scope",
        "not_licensed_not_in_v1_scope",
    ),
    "redirect_to_source": (
        "permitted",
        "verbatim_supplier_url_only_with_context_match",
    ),
}


# --- presentation of a gate -------------------------------------------------

#: Chinese field names. A gate result is only useful to the page if every code
#: has a name the user can read, so this mapping is exhaustive by design.
FIELD_LABELS: dict[str, str] = {
    ROOM_EXCLUSIVITY: "整间客房独享",
    PRIVATE_BATHROOM: "房内独立卫浴",
    INVENTORY_QUANTITY: "足量库存",
    INSTANT_BOOKING: "立订标记",
    PRICE_INCLUDES_TAX: "展示价含税",
    PRICE_UNIT: "计价单位",
    NIGHTLY_BREAKDOWN: "逐夜价格",
    DEPOSIT_AND_FEES: "押金与到店必付",
    OCCUPANCY_ECHO: "入住组合回显",
    BOOKING_LINK: "来源预订链接",
    CANCELLATION: "取消规则",
    GUEST_SCORE: "住客评分",
}

#: One sentence per field explaining *why* it sits in that bucket. These are
#: static because they describe the evidence rule, not a particular response.
FIELD_NOTES: dict[str, str] = {
    ROOM_EXCLUSIVITY: "按国内默认推定整间客房独享，未取得来源字段。",
    PRIVATE_BATHROOM: "按国内默认推定房内独卫；房型名写了公共卫浴／床位则不推定。",
    INVENTORY_QUANTITY: "来源自称剩余房量，需不少于本次房数；不等于锁价。",
    INSTANT_BOOKING: "来源是否标记需人工放盘审批，不表示剩余数量。",
    PRICE_INCLUDES_TAX: "境内展示价按已含税费与服务费处理，不另加税。",
    PRICE_UNIT: "来源未注明计价单位，折算金额只能标“推定”。",
    NIGHTLY_BREAKDOWN: "来源只给均价，拿不到逐夜价格。",
    DEPOSIT_AND_FEES: "来源没有报价级押金／到店必付字段，到店可能另有支出。",
    OCCUPANCY_ECHO: "来源不回显成人数／儿童／房数，按请求参数＋剩余房量＋观察时间推定。",
    BOOKING_LINK: "来源详情不返回预订链接，本页不提供跳转。",
    CANCELLATION: "来源给出取消条款文本，是否即时生效仍未核实。",
    GUEST_SCORE: "评分只作参考，不参与资格判定。",
}

#: What a *verified* row means, per field, independent of source. Used only when
#: the verdict did not produce its own sentence — otherwise a verified row could
#: inherit another source's caveat (e.g. "this detail returns no booking link"
#: printed next to a source that does return one).
VERIFIED_NOTES: dict[str, str] = {
    INVENTORY_QUANTITY: "来源自称剩余房量，需不少于本次房数；不等于锁价。",
    PRICE_INCLUDES_TAX: "来源口径为展示价已含税，不另加税。",
    BOOKING_LINK: "来源返回原样预订链接，参数已按本次条件核对；仍不等于来源已确认可订。",
    INSTANT_BOOKING: "来源明确标记是否需要人工放盘，不表示剩余数量。",
}
VERIFIED_NOTE_FALLBACK = "来源已提供该字段的明确证据。"


@dataclass(frozen=True)
class GateItemView:
    """One row a page can render directly, with no further interpretation."""

    field: str
    label: str
    status: str
    note: str


def gate_items(gate: LayerGate) -> tuple[GateItemView, ...]:
    """Flatten a gate into display rows in a stable, predictable order.

    ``caveats`` are reported as ``unknown`` on purpose: they are fields that do
    not block a reference card but must still be shown as unproven, so the page
    can never quietly drop them.
    """
    bucket = {
        **{field: "verified" for field in gate.verified},
        **{field: "presumed" for field in gate.presumed},
        **{field: "accepted" for field in gate.accepted},
        **{field: "blocked" for field in gate.blocked},
        **{field: "unknown" for field in gate.caveats},
    }
    # Prefer the sentence this verdict actually produced: the static table
    # describes the *rule*, not this source's field state.
    produced = dict(gate.notes)
    order = list(FIELD_LABELS)
    extra = [field for field in bucket if field not in order]

    def note_for(field: str) -> str:
        text = produced.get(field)
        if text:
            return text
        if bucket[field] == "verified":
            return VERIFIED_NOTES.get(field, VERIFIED_NOTE_FALLBACK)
        return FIELD_NOTES.get(field, "")

    return tuple(
        GateItemView(
            field=field,
            label=FIELD_LABELS.get(field, field),
            status=bucket[field],
            note=note_for(field),
        )
        for field in order + extra
        if field in bucket
    )
