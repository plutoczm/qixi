"""Deterministic qualification and fee engine (P6).

Pure by construction: same input -> same output, no I/O, no clock, no model.
All money is integer fen, so no float drift can reach a total.

Why three verdicts instead of a boolean: a source can *refuse to answer*
(no stock field, no score scale). Collapsing that into ``False`` makes a
missing field look like a failed condition, and collapsing it into ``True``
invents a guarantee. ``unknown`` is the only honest third answer, and it is
what separates a 合格报价 from a 参考报价 on the page.

Tiers (never a bare boolean either):

* ``qualified``  -- every condition passed (presumptions allowed, but each one
  is flagged) and the budget did not fail. Presumptions do not make a quote
  bookable; they only make it displayable as a qualified *reference*.
* ``reference``  -- no condition failed, but at least one is unknown. Show the
  price, never call it qualified.
* ``excluded``   -- a condition or the budget failed.

Invariants enforced here (each has a test that breaks if someone "simplifies"
it away):

1. An already-included tax or fee is never added a second time.
2. An unverified cost is never subtracted from the total, and never counted
   as zero either: it is listed as unverified and excluded from the sum.
3. A refundable deposit is never part of consumption.
4. Inventory is never multiplied: ``remaining < rooms`` fails the quote
   outright instead of doubling a single room's price.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from .qualification import (
    DORMITORY_MARKERS,
    PRESUMED_TAX_INCLUDED,
    inventory_from_instant_booking,
    inventory_from_remaining,
    private_bathroom,
    room_form,
)

Verdict = Literal["pass", "fail", "unknown"]
Tier = Literal["qualified", "reference", "excluded"]
PriceUnit = Literal["per_room_night", "per_stay", "unstated"]
Period = Literal["stay", "room_night", "room_stay", "person_night"]
#: How a dormitory-style rate plan is priced once the user accepts a bed.
#: ``per_person`` only when the room name says 床位/上下铺; a name that says
#: 多人间/宿舍 without saying 床位 cannot be told apart from a whole-dorm price,
#: so it stays ``unclear`` and never enters the total.
BedBasis = Literal["per_person", "unclear"]

#: Wording that prices one bed, not the whole room.
BED_PLACE_MARKERS: tuple[str, ...] = ("床位", "上下铺", "通铺")

CONDITION_LABELS: dict[str, str] = {
    "room_form": "整间客房",
    "private_bathroom": "房内独立卫浴",
    "inventory": "足量库存",
    "occupancy_capacity": "房型可住人数",
    "guest_score": "住客评分",
    "cancellation": "取消规则",
}

#: Markers that mean a rate plan cannot be cancelled. Checked *before* the
#: free-cancel markers on purpose: "不可取消" contains "可取消", so a naive
#: substring test would read a non-refundable plan as freely cancellable.
NON_REFUNDABLE_MARKERS: tuple[str, ...] = (
    "不可取消", "不可退", "不退款", "不可退订", "不可变更",
)
FREE_CANCEL_MARKERS: tuple[str, ...] = (
    "免费取消", "免费退", "免费退订",
)


def _normalise(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def _marker(text: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        if marker in text:
            return marker
    return None


def bed_pricing_basis(room_name: str | None, accepts_dormitory: bool) -> BedBasis | None:
    """How to read the amount once the user accepts a bed place.

    ``None`` means "not a bed-place case"; the caller keeps its normal pricing
    rule. The user's acceptance never changes a room that is not a bed place --
    accepting a bed is not a licence to relax an ordinary hotel room.
    """
    if not accepts_dormitory or type(room_name) is not str or not room_name.strip():
        return None
    text = _normalise(room_name)
    if _marker(text, BED_PLACE_MARKERS) is not None:
        return "per_person"
    if _marker(text, DORMITORY_MARKERS) is not None:
        return "unclear"
    return None


@dataclass(frozen=True)
class Judgement:
    """One condition's verdict, with the evidence basis it rests on."""

    condition: str
    verdict: Verdict
    basis: str
    detail: str
    #: True when the verdict rests on an approved default, not on a source
    #: field. A presumed pass is still a pass for the reference layer, but it
    #: must be printed as 推定 and can never satisfy the booking layer.
    presumed: bool = False
    #: True when the user explicitly relaxed this condition for this query
    #: (today only: accepting a bed place). This is neither evidence nor our
    #: presumption -- it is the user's own decision and must be printed as such,
    #: so it can never be shown as 推定 or as 已核实.
    accepted: bool = False

    @property
    def label(self) -> str:
        return CONDITION_LABELS.get(self.condition, self.condition)


# --- money ------------------------------------------------------------------

def fee_total_fen(
    amount_fen: int, per: Period, *, nights: int, rooms: int, persons: int,
) -> int:
    """Expand a unit price into a stay total, in integer fen.

    ``persons`` is the total head count (per-room adults plus children, times
    rooms), because a per-person charge applies to everyone staying.
    """
    if type(amount_fen) is not int or amount_fen < 0:
        raise ValueError("amount_fen must be a non-negative integer")
    _check_stay(nights, rooms)
    if per == "stay":
        return amount_fen
    if per == "room_night":
        return amount_fen * nights * rooms
    if per == "room_stay":
        return amount_fen * rooms
    if per == "person_night":
        if type(persons) is not int or persons < 0:
            raise ValueError("persons must be a non-negative integer")
        return amount_fen * nights * persons
    raise ValueError(f"unknown period: {per}")


def _check_stay(nights: int, rooms: int) -> None:
    if type(nights) is not int or nights < 1:
        raise ValueError("nights must be a positive integer")
    if type(rooms) is not int or rooms < 1:
        raise ValueError("rooms must be a positive integer")


@dataclass(frozen=True)
class RoomCharge:
    total_fen: int | None
    basis: str
    presumed: bool
    note: str


def room_charge(
    amount_fen: int | None, *, nights: int, rooms: int, price_unit: PriceUnit,
    source_id: str, bed_basis: BedBasis | None = None, persons: int | None = None,
) -> RoomCharge:
    """Turn one published amount into a stay total, or refuse to.

    A per-stay amount is not multiplied by nights. An amount whose unit the
    source never states is multiplied only as an approved *presumption*, and
    the note says the figure would be an overestimate if the supplier meant a
    whole-stay price.

    ``bed_basis`` only applies where the source states no unit: a stated unit
    always wins, because the supplier described its own number. A bed place the
    user accepted is priced per person per night; a dormitory name that never
    says 床位 stays unpriced, since a whole-dorm price and a bed price are
    indistinguishable there.
    """
    _check_stay(nights, rooms)
    if type(amount_fen) is not int or amount_fen < 0:
        return RoomCharge(None, "amount_missing", False, "来源未返回可用金额。")
    if price_unit == "per_room_night":
        return RoomCharge(
            amount_fen * nights * rooms, "official_per_room_night", False,
            f"来源为每间每晚价：{amount_fen} 分 × {nights} 晚 × {rooms} 间。",
        )
    if price_unit == "per_stay":
        if rooms > 1:
            return RoomCharge(
                amount_fen, "official_per_stay_rooms_unstated", True,
                f"来源为整单价，未说明是否含 {rooms} 间：按整单一次性计入，"
                "若来源本意是每间整单，此值偏低。",
            )
        return RoomCharge(
            amount_fen, "official_per_stay", False,
            f"来源为整单价：按整单 {amount_fen} 分计入，不乘晚数。",
        )
    if source_id == "flyai":
        return RoomCharge(
            None, "currency_unconfirmed", True,
            "公开样例缺币种，不得折算整住期金额。",
        )
    if bed_basis == "unclear":
        return RoomCharge(
            None, "bed_price_basis_unclear", True,
            "房型写了多人间／宿舍但没有写是否按床位计价，整间价与床价无法区分，"
            "金额不并入合计。",
        )
    if bed_basis == "per_person":
        beds = persons if type(persons) is int and persons >= 1 else 1
        return RoomCharge(
            amount_fen * nights * beds, "user_accepted_bed_per_person", True,
            f"你已接受床位：按 {amount_fen} 分 × {nights} 晚 × {beds} 人（床）"
            "推定为整住期金额；若来源本意是整间价，此值偏高。",
        )
    return RoomCharge(
        amount_fen * nights * rooms, "user_approved_average_times_nights", True,
        f"来源未注明计价单位：按 {amount_fen} 分 × {nights} 晚 × {rooms} 间推定整住期金额；"
        "若来源为整单价，此值偏高。",
    )


@dataclass(frozen=True)
class FeeInput:
    """A mandatory cost the source reports, with its own evidence state."""

    label: str
    amount_fen: int
    per: Period = "room_night"
    verified: bool = True
    #: True when the source says this cost is already inside the room charge.
    included: bool = False


@dataclass(frozen=True)
class DepositInput:
    """A deposit is not consumption. Non-refundable ones are."""

    amount_fen: int
    refundable: bool | None = None
    verified: bool = True


@dataclass(frozen=True)
class DiscountInput:
    """A potential saving. Only a verified eligibility may reduce the total."""

    label: str
    amount_fen: int
    eligible: bool | None = None


@dataclass(frozen=True)
class FeeLine:
    kind: Literal["room_charge", "mandatory_fee", "deposit", "discount"]
    label: str
    amount_fen: int
    per: Period
    total_fen: int
    #: Whether this line is inside ``total_known_fen``.
    counted: bool
    note: str = ""


@dataclass(frozen=True)
class FeeBreakdown:
    nights: int
    rooms: int
    room_charge_fen: int | None
    mandatory_fees_fen: int
    deposit_fen: int | None
    deposit_refundable: bool | None
    discount_fen: int | None
    discount_applied_fen: int
    #: Sum of the *known* consumption lines. Never a promise of "all fees".
    total_known_fen: int | None
    per_room_night_fen: int | None
    tax_basis: str
    unknown_items: tuple[str, ...] = ()
    lines: tuple[FeeLine, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Only a breakdown with no unverified cost may claim an all-in total."""
        return not self.unknown_items


def build_fees(
    *,
    amount_fen: int | None,
    price_unit: PriceUnit,
    source_id: str,
    tax_included: bool | None,
    nights: int,
    rooms: int,
    adults: int,
    children: int = 0,
    mandatory_fees: tuple[FeeInput, ...] = (),
    deposit: DepositInput | None = None,
    discounts: tuple[DiscountInput, ...] = (),
    room_name: str | None = None,
    accepts_dormitory: bool = False,
) -> FeeBreakdown:
    """Assemble the cost picture, keeping unknown costs out of the sum."""
    _check_stay(nights, rooms)
    persons = (adults + children) * rooms
    bed_basis = bed_pricing_basis(room_name, accepts_dormitory)
    charge = room_charge(
        amount_fen, nights=nights, rooms=rooms, price_unit=price_unit,
        source_id=source_id, bed_basis=bed_basis, persons=persons,
    )
    lines: list[FeeLine] = []
    notes: list[str] = [charge.note]
    unknown: list[str] = []

    if charge.total_fen is None:
        lines.append(FeeLine("room_charge", "房费", 0, "stay", 0, False, charge.note))
        unknown.append(
            "房费计价口径未定，未计入合计。"
            if charge.basis == "bed_price_basis_unclear"
            else "房费金额缺失，未计入合计。"
        )
    else:
        lines.append(FeeLine(
            "room_charge", "房费", amount_fen if type(amount_fen) is int else 0,
            "stay" if price_unit == "per_stay"
            else "person_night" if charge.basis == "user_accepted_bed_per_person"
            else "room_night",
            charge.total_fen, True, charge.note,
        ))

    explicit_tax = next((fee for fee in mandatory_fees if fee.label == "税费"), None)
    if tax_included is True:
        tax_basis = "source_states_tax_included"
        notes.append("来源声明展示价含税，不再另加税费。")
        if explicit_tax is not None:
            notes.append("来源另列了税费条目，已忽略以避免重复计入。")
    elif tax_included is False:
        tax_basis = "source_states_tax_excluded"
        unknown.append("来源声明不含税，但税额未提供，未计入合计。")
    elif source_id in PRESUMED_TAX_INCLUDED:
        tax_basis = "domestic_default_tax_included"
        notes.append(
            "来源未注明是否含税：按境内展示价含税口径推定，不另加；该口径不是来源确认。"
        )
    else:
        tax_basis = "tax_treatment_unknown"
        unknown.append("税费是否已含未核实，未计入合计。")

    mandatory_total = 0
    for fee in mandatory_fees:
        if explicit_tax is fee and tax_included is True:
            continue
        total = fee_total_fen(fee.amount_fen, fee.per, nights=nights, rooms=rooms, persons=persons)
        if fee.included:
            lines.append(FeeLine(
                "mandatory_fee", fee.label, fee.amount_fen, fee.per, total, False,
                "来源称该项已含在房费里，不重复计入。",
            ))
            continue
        if not fee.verified:
            lines.append(FeeLine(
                "mandatory_fee", fee.label, fee.amount_fen, fee.per, total, False,
                "该项未核实，单列但不计入合计。",
            ))
            unknown.append(f"{fee.label}（未核实）未计入合计。")
            continue
        mandatory_total += total
        lines.append(FeeLine(
            "mandatory_fee", fee.label, fee.amount_fen, fee.per, total, True,
            "已核实的必要费用，计入消费。",
        ))

    deposit_fen: int | None = None
    deposit_refundable: bool | None = None
    if deposit is not None:
        deposit_fen = fee_total_fen(
            deposit.amount_fen, "stay", nights=nights, rooms=rooms, persons=persons,
        )
        deposit_refundable = deposit.refundable
        if deposit.refundable is False:
            mandatory_total += deposit_fen
            lines.append(FeeLine(
                "mandatory_fee", "不可退押金", deposit.amount_fen, "stay",
                deposit_fen, True, "不可退押金按消费计入。",
            ))
        elif deposit.refundable is True:
            lines.append(FeeLine(
                "deposit", "可退押金", deposit.amount_fen, "stay", deposit_fen, False,
                "可退押金单列，不计入消费。",
            ))
        else:
            lines.append(FeeLine(
                "deposit", "押金（可退性未核实）", deposit.amount_fen, "stay",
                deposit_fen, False, "押金可退性未核实，未计入消费。",
            ))
            unknown.append("押金是否可退未核实，未计入消费。")

    discount_fen: int | None = None
    applied = 0
    for discount in discounts:
        discount_fen = (discount_fen or 0) + discount.amount_fen
        if discount.eligible is True:
            applied += discount.amount_fen
            lines.append(FeeLine(
                "discount", discount.label, discount.amount_fen, "stay",
                discount.amount_fen, True, "资格已核实，从消费中扣除。",
            ))
        else:
            lines.append(FeeLine(
                "discount", discount.label, discount.amount_fen, "stay",
                discount.amount_fen, False,
                "优惠资格未核实：单列潜在优惠，不抵扣预算。",
            ))

    total = None
    per_night = None
    if charge.total_fen is not None:
        total = charge.total_fen + mandatory_total - applied
        divisor = nights * rooms
        per_night = total // divisor
        remainder = total % divisor
        if remainder:
            notes.append(f"每房每晚取整计算，余 {remainder} 分未计入单间单晚。")

    return FeeBreakdown(
        nights=nights,
        rooms=rooms,
        room_charge_fen=charge.total_fen,
        mandatory_fees_fen=mandatory_total,
        deposit_fen=deposit_fen,
        deposit_refundable=deposit_refundable,
        discount_fen=discount_fen,
        discount_applied_fen=applied,
        total_known_fen=total,
        per_room_night_fen=per_night,
        tax_basis=tax_basis,
        unknown_items=tuple(unknown),
        lines=tuple(lines),
        notes=tuple(notes),
    )


# --- conditions -------------------------------------------------------------

def _dormitory_marker(room_name: str | None) -> str | None:
    if type(room_name) is not str or not room_name.strip():
        return None
    return _marker(_normalise(room_name), DORMITORY_MARKERS)


def room_form_judgement(
    room_name: str | None, accepts_dormitory: bool = False,
) -> Judgement:
    assumption = room_form(room_name)
    if assumption.satisfied:
        return Judgement(
            "room_form", "pass", assumption.basis, assumption.label, presumed=True,
        )
    marker = _dormitory_marker(room_name)
    if accepts_dormitory and marker is not None:
        return Judgement(
            "room_form", "pass", f"user_accepted_bed:{marker}",
            f"房型标注“{marker}”，你已明确接受床位，不再按整间过夜客房要求。",
            accepted=True,
        )
    return Judgement("room_form", "fail", assumption.basis, assumption.label)


def private_bathroom_judgement(
    room_name: str | None, accepts_dormitory: bool = False,
) -> Judgement:
    assumption = private_bathroom(room_name)
    if assumption.satisfied:
        return Judgement(
            "private_bathroom", "pass", assumption.basis, assumption.label,
            presumed=True,
        )
    marker = _dormitory_marker(room_name)
    if accepts_dormitory and marker is not None:
        return Judgement(
            "private_bathroom", "pass", f"user_accepted_shared_bathroom:{marker}",
            f"房型标注“{marker}”；你接受床位即同时接受这类房型常见的共享卫浴。",
            accepted=True,
        )
    return Judgement(
        "private_bathroom", "fail", assumption.basis, assumption.label,
    )


def inventory_judgement(
    remaining: int | None, on_request: bool | None, rooms: int,
) -> Judgement:
    if type(remaining) is int:
        assessment = inventory_from_remaining(remaining, rooms)
    else:
        assessment = inventory_from_instant_booking(on_request, rooms)
    verdict: Verdict = "unknown"
    if assessment.status == "sufficient":
        verdict = "pass"
    elif assessment.status == "insufficient":
        verdict = "fail"
    return Judgement("inventory", verdict, assessment.basis, assessment.detail)


def occupancy_capacity_judgement(
    max_occupancy: int | None, *, adults: int, children: int,
) -> Judgement:
    """Never invent capacity, but allow the approved 2-adult domestic default."""
    if type(adults) is not int or adults < 1 or type(children) is not int or children < 0:
        raise ValueError("adults must be >= 1 and children >= 0")
    need = adults + children
    if type(max_occupancy) is int:
        if need <= max_occupancy:
            return Judgement(
                "occupancy_capacity", "pass", "official_max_occupancy",
                f"来源房型可住 {max_occupancy} 人 ≥ 本次每房 {need} 人。",
            )
        return Judgement(
            "occupancy_capacity", "fail", "official_max_occupancy",
            f"来源房型可住 {max_occupancy} 人 < 本次每房 {need} 人。",
        )
    if need <= 2:
        return Judgement(
            "occupancy_capacity", "pass", "domestic_default_two_adults",
            "来源未回显可住人数；按境内按间计价默认（一间普通客房容纳 2 人）推定，"
            "非来源确认。",
            presumed=True,
        )
    return Judgement(
        "occupancy_capacity", "unknown", "capacity_not_reported",
        f"来源未回显可住人数，无法证明每房可住 {need} 人。",
    )


def guest_score_judgement(
    score: float | None, scale: str | None, threshold: float | None,
) -> Judgement:
    """R04: a verifiable 5-point guest score, or no threshold at all.

    A star rating is not a guest score, and a score on an unstated scale is
    never converted -- both are refused rather than guessed.
    """
    if score is None:
        return Judgement(
            "guest_score", "pass", "r04_threshold_not_enabled_when_absent",
            "未取得住客评分：按 R04 不启用该评分门槛（星级不充当住客评分）。",
        )
    if threshold is None:
        return Judgement(
            "guest_score", "pass", "threshold_not_required",
            "本次未设置住客评分门槛。",
        )
    if scale != "5":
        return Judgement(
            "guest_score", "unknown", "score_scale_not_stated",
            f"来源给出评分 {score}，但量表未注明；不换算，也不作为通过依据。",
        )
    if score >= threshold:
        return Judgement(
            "guest_score", "pass", "official_five_point_score",
            f"住客评分 {score}（5 分制）≥ 门槛 {threshold}。",
        )
    return Judgement(
        "guest_score", "fail", "official_five_point_score",
        f"住客评分 {score}（5 分制）< 门槛 {threshold}。",
    )


def cancellation_judgement(
    text: str | None, *, verified: bool | None, require_free_cancel: bool,
) -> Judgement:
    """Free-cancel wording is a presumption; a structured flag is evidence."""
    # r72: real Tuniu cancellation text already ends with "。". Every branch
    # below appends its own full stop, which produced "支付酒店。。" on the
    # first real qualified card. Strip the source's terminal punctuation once,
    # then let the branch supply exactly one.
    shown = ((text or "").strip() or "取消规则待核实").rstrip("。．.!！?？")
    if not require_free_cancel:
        return Judgement(
            "cancellation", "pass", "free_cancel_not_required",
            f"本次不要求免费取消；来源条款：{shown}。",
        )
    if verified is True:
        return Judgement(
            "cancellation", "pass", "structured_free_cancel_flag",
            f"来源结构化标记为可取消；条款：{shown}。",
        )
    if verified is False:
        return Judgement(
            "cancellation", "fail", "structured_non_refundable_flag",
            f"来源结构化标记为不可取消；条款：{shown}。",
        )
    normalised = _normalise(shown)
    # Order matters: "不可取消" contains "可取消".
    if any(marker in normalised for marker in NON_REFUNDABLE_MARKERS):
        return Judgement(
            "cancellation", "fail", "source_text_non_refundable",
            f"来源条款写明不可取消：{shown}。",
        )
    if any(marker in normalised for marker in FREE_CANCEL_MARKERS):
        return Judgement(
            "cancellation", "pass", "source_text_free_cancel",
            f"来源条款提到免费取消，但未取得结构化标记；按条款文本推定。条款：{shown}。",
            presumed=True,
        )
    return Judgement(
        "cancellation", "unknown", "free_cancel_unverifiable",
        f"要求免费取消，但来源条款无法判定是否含免费取消：{shown}。",
    )


# --- budget -----------------------------------------------------------------

BudgetBasis = Literal[
    "no_budget",
    "single_night_known_costs",
    "nightly_verified",
    "average_presumed",
    "nightly_incomplete",
    "total_unknown",
]


@dataclass(frozen=True)
class BudgetAssessment:
    verdict: Verdict
    basis: BudgetBasis
    detail: str
    budget_fen: int | None = None
    compared_fen: int | None = None


def check_budget(
    fees: FeeBreakdown,
    nightly_fen: tuple[int, ...],
    budget_fen: int | None,
    *,
    nights: int,
    rooms: int,
) -> BudgetAssessment:
    """Compare against a per-room-per-night budget.

    Two different bases, never merged: with a complete nightly breakdown we
    can say every night fits; with only an averaged amount we can only say the
    *average* fits. The second one may still pass (user decision 2026-11-07),
    but its detail text always carries the risk, so no page can present it as
    "every night verified".
    """
    _check_stay(nights, rooms)
    if budget_fen is None:
        return BudgetAssessment("pass", "no_budget", "本次未设置每房每晚预算门槛。")
    if type(budget_fen) is not int or budget_fen < 0:
        raise ValueError("budget_fen must be a non-negative integer")

    if len(nightly_fen) == nights and all(type(x) is int and x >= 0 for x in nightly_fen):
        worst = max(nightly_fen)
        if worst > budget_fen:
            return BudgetAssessment(
                "fail", "nightly_verified",
                f"逐夜价已取得：最高一晚 {worst} 分/间 > 每房每晚预算 {budget_fen} 分。",
                budget_fen, worst,
            )
        return BudgetAssessment(
            "pass", "nightly_verified",
            f"逐夜价已取得：最高一晚 {worst} 分/间 ≤ 每房每晚预算 {budget_fen} 分。",
            budget_fen, worst,
        )
    if nightly_fen:
        return BudgetAssessment(
            "unknown", "nightly_incomplete",
            f"逐夜价只有 {len(nightly_fen)}/{nights} 晚，无法逐晚校验预算。",
            budget_fen, None,
        )

    per_night = fees.per_room_night_fen
    if per_night is None:
        return BudgetAssessment(
            "unknown", "total_unknown", "金额缺失，无法比较预算。", budget_fen, None,
        )
    if per_night > budget_fen:
        return BudgetAssessment(
            "fail", "average_presumed" if nights > 1 else "single_night_known_costs",
            f"已知费用折算每房每晚 {per_night} 分 > 预算 {budget_fen} 分。",
            budget_fen, per_night,
        )
    if nights == 1:
        # A single night still cannot promise "budget met" when fees are
        # unknown: the sum is the *known* part, and saying so is the only way
        # the invariant "unknown is never zero" survives into the wording.
        caveat = (
            f"；另有 {len(fees.unknown_items)} 项未核实费用未计入" if fees.unknown_items else ""
        )
        return BudgetAssessment(
            "pass", "single_night_known_costs",
            f"单晚已知费用 {per_night} 分/间 ≤ 预算 {budget_fen} 分{caveat}。",
            budget_fen, per_night,
        )
    risk = "未取得逐夜价，无法排除其中一晚超预算"
    if fees.unknown_items:
        risk += f"；另有 {len(fees.unknown_items)} 项未核实费用未计入"
    return BudgetAssessment(
        "pass", "average_presumed",
        f"均价口径通过：已知费用折算每房每晚 {per_night} 分 ≤ 预算 {budget_fen} 分；"
        f"{risk}。",
        budget_fen, per_night,
    )


# --- assessment -------------------------------------------------------------

@dataclass(frozen=True)
class QuoteInput:
    """One rate plan, fully described. No hidden state, no lookups."""

    source_id: str
    room_name: str | None
    amount_fen: int | None
    price_unit: PriceUnit = "unstated"
    tax_included: bool | None = None
    remaining: int | None = None
    on_request: bool | None = None
    nights: int = 1
    rooms: int = 1
    adults: int = 2
    children: int = 0
    max_occupancy: int | None = None
    guest_score: float | None = None
    guest_score_scale: str | None = None
    require_guest_score: float | None = 4.5
    cancellation_text: str | None = None
    cancellation_verified: bool | None = None
    require_free_cancel: bool = False
    nightly_fen: tuple[int, ...] = ()
    mandatory_fees: tuple[FeeInput, ...] = ()
    deposit: DepositInput | None = None
    discounts: tuple[DiscountInput, ...] = ()
    budget_fen: int | None = None
    #: The user explicitly said a bed place is acceptable for this query. It
    #: relaxes exactly two conditions (whole room, private bathroom) and changes
    #: how an unstated amount is multiplied (per person, not per room).
    accepts_dormitory: bool = False


@dataclass(frozen=True)
class Assessment:
    tier: Tier
    conditions: tuple[Judgement, ...]
    fees: FeeBreakdown
    budget: BudgetAssessment
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def qualified(self) -> bool:
        return self.tier == "qualified"

    @property
    def presumed(self) -> tuple[Judgement, ...]:
        return tuple(j for j in self.conditions if j.presumed)


def assess(item: QuoteInput) -> Assessment:
    """Qualify one quote. Deterministic: no clock, no network, no model."""
    _check_stay(item.nights, item.rooms)
    conditions = (
        room_form_judgement(item.room_name, item.accepts_dormitory),
        private_bathroom_judgement(item.room_name, item.accepts_dormitory),
        inventory_judgement(item.remaining, item.on_request, item.rooms),
        occupancy_capacity_judgement(
            item.max_occupancy, adults=item.adults, children=item.children,
        ),
        guest_score_judgement(
            item.guest_score, item.guest_score_scale, item.require_guest_score,
        ),
        cancellation_judgement(
            item.cancellation_text,
            verified=item.cancellation_verified,
            require_free_cancel=item.require_free_cancel,
        ),
    )
    fees = build_fees(
        amount_fen=item.amount_fen,
        price_unit=item.price_unit,
        source_id=item.source_id,
        tax_included=item.tax_included,
        nights=item.nights,
        rooms=item.rooms,
        adults=item.adults,
        children=item.children,
        mandatory_fees=item.mandatory_fees,
        deposit=item.deposit,
        discounts=item.discounts,
        room_name=item.room_name,
        accepts_dormitory=item.accepts_dormitory,
    )
    budget = check_budget(
        fees, item.nightly_fen, item.budget_fen, nights=item.nights, rooms=item.rooms,
    )
    tier, reasons = _tier(conditions, fees, budget)
    return Assessment(tier, conditions, fees, budget, reasons)


def _tier(
    conditions: tuple[Judgement, ...], fees: "FeeBreakdown",
    budget: BudgetAssessment,
) -> tuple[Tier, tuple[str, ...]]:
    failed = [row for row in conditions if row.verdict == "fail"]
    if failed or budget.verdict == "fail":
        reasons = [
            f"{row.label}：{row.detail}" for row in failed
        ]
        if budget.verdict == "fail":
            reasons.append(f"预算：{budget.detail}")
        return "excluded", tuple(reasons)
    unknown = [f"{row.label}：{row.detail}" for row in conditions if row.verdict == "unknown"]
    if budget.verdict == "unknown":
        unknown.append(f"预算：{budget.detail}")
    # A quote with no usable amount cannot be "qualified": every condition may
    # pass while the number itself is missing, and calling that qualified would
    # print a green verdict on a card that shows no price. Ranking already
    # skips such quotes, but the count and the badge still had to agree with it.
    if fees.total_known_fen is None:
        unknown.append("房费：金额缺失，无法给出这个价格的结论。")
    if unknown:
        return "reference", tuple(unknown)
    return "qualified", ()


def lowest_qualified(assessments) -> int | None:
    """Index of the cheapest qualified quote, or ``None`` if nothing qualifies.

    Accepts anything exposing ``tier`` and ``fees.per_room_night_fen`` -- the
    rule-layer :class:`Assessment` and the wire ``Qualification`` both qualify,
    so the page and the API rank with one implementation instead of two that
    drift apart.

    Ranking is per room per night, so a 3-night quote cannot beat a 1-night one
    merely by covering more nights. Quotes without a known amount never win.
    """
    best: tuple[int, int] | None = None
    for index, assessment in enumerate(assessments):
        if assessment.tier != "qualified":
            continue
        # ``qualified`` already requires a known total (see ``_tier``), so the
        # per-room-night figure is always present here. An extra None check
        # would be an unreachable branch dressed up as defensive code.
        value = assessment.fees.per_room_night_fen
        key = (value, index)
        if best is None or key < best:
            best = key
    return None if best is None else best[1]
