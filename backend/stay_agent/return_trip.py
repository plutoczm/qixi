"""Deterministic return-trip evidence and comprehensive-cost rules (P7).

Pure by construction: same input -> same output, no I/O, no clock, no model,
no float. All money is integer fen.

User decisions 2026-11-07 (recorded in spec R07/R08 and plan P7):

* Ranking keys are ``guest score -> review count -> then the remaining
  tie-breakers``. Total cost is NOT the primary key any more.
* A stay is an eligible candidate only inside the 100-200 CNY per room per
  night band (10000-20000 fen). This is a *selection prerequisite* for P7,
  not a rewrite of the R02 budget rule.
* The return fare is observed in a 0-16 CNY range (0-1600 fen). The
  **admission gate stays at 1500 fen** (R07 / AC07): a fare of 1500-1600 fen
  is visible but fails the gate, so AC07/AC08 boundaries are not silently
  moved. Unknown / expired / a range whose upper bound exceeds the gate never
  passes (AC10).
* There is no "planned return trips" input. One return leg is counted once;
  multi-trip extrapolation is explicitly out of scope.

Honesty invariants (each one has a test that fails if it is "simplified"):

1. A missing guest score or review count makes a plan ``not_comparable``.
   It never falls back to a total-cost order, and a hotel star rating is
   never substituted for a guest score (R04).
2. An unknown return fare is never zero and never enters the total; it is
   listed as an unverified item instead (R07/R08).
3. Walking <=1500 m OR driving <=900 s passes independently. Walking time
   never substitutes distance, and walking distance never converts to a
   driving duration (R06 / AC05 / AC06).
4. Night entrance and future opening are never promised. A current fare is
   never presented as a future late-night locked price (AC09).
5. The total is only a ``known costs subtotal``; unverified costs are listed
   rather than silently zeroed (R03/R08).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Verdict = Literal["pass", "fail", "unknown"]
Tier = Literal["rankable", "reference_only", "excluded"]
FareState = Literal["known", "range", "unknown", "expired"]
OpeningVerdict = Literal["open", "closed", "unknown"]

#: P7 selection prerequisite (user 2026-11-07), CNY integer fen per room night.
STAY_MIN_FEN = 10000
STAY_MAX_FEN = 20000
#: 0-16 CNY observable fare range (user 2026-11-07).
FARE_OBSERVABLE_MAX_FEN = 1600
#: R07 / AC07 admission gate, deliberately NOT moved to 1600.
FARE_GATE_FEN = 1500
WALK_MAX_METERS = 1500
DRIVE_MAX_SECONDS = 900

RISK_NOTES: tuple[str, ...] = (
    "当前估价不等于未来深夜实价，也不构成锁价。",
    "夜间营业、实际入口与未来到访时段未核实，不承诺可用。",
    "司机接驾等待与未来路况未计入驾车预计耗时。",
    "这是参考层结论，不代表可订、已核实库存或实付总价。",
)


@dataclass(frozen=True)
class PlanInput:
    """One stay + one restaurant branch + one return leg.

    ``guest_score_tenths`` is a 5-point guest score scaled by 10 (4.6 -> 46),
    kept as an integer so no float reaches the ranking. ``None`` everywhere
    means "not supplied by the source", which is ``unknown``, never ``fail``.
    """

    plan_id: str
    per_room_night_fen: int | None = None
    stay_known_fen: int | None = None
    fare_lower_fen: int | None = None
    fare_upper_fen: int | None = None
    fare_state: FareState = "unknown"
    walk_meters: int | None = None
    #: Walking *duration* is kept for display only. R06 judges walking by
    #: distance, so this value must never influence the verdict (AC06).
    walk_duration_seconds: int | None = None
    drive_seconds: int | None = None
    guest_score_tenths: int | None = None
    review_count: int | None = None
    #: A cheap fare only counts when an ordinary vehicle and the user's
    #: discount eligibility are verified (AC33). The current source returns
    #: both flags as False, so this stays unknown by default.
    fare_ordinary_verified: bool = False
    night_access_verified: bool = False
    future_opening_verified: bool = False
    subway_required: bool = False
    subway_within_limit: bool | None = None
    branch_label: str | None = None
    #: P6 qualification is an upstream hard gate (R08: all hard conditions
    #: first). A quote that is merely 参考/排除 never reaches P7 ranking.
    upstream_qualified: bool = True


@dataclass(frozen=True)
class Condition:
    key: str
    label: str
    verdict: Verdict
    basis: str
    detail: str


@dataclass(frozen=True)
class PlanAssessment:
    plan_id: str
    tier: Tier
    conditions: tuple[Condition, ...]
    stay_known_fen: int | None
    fare_used_fen: int | None
    total_known_fen: int | None
    guest_score_tenths: int | None
    review_count: int | None
    notes: tuple[str, ...]
    risks: tuple[str, ...]

    @property
    def verdicts(self) -> dict[str, Verdict]:
        return {row.key: row.verdict for row in self.conditions}


@dataclass(frozen=True)
class RankedPlan:
    plan_id: str
    position: int
    guest_score_tenths: int
    review_count: int
    total_known_fen: int | None
    tie_break: str


@dataclass(frozen=True)
class RankingResult:
    status: Literal["ranked", "not_comparable"]
    order: tuple[RankedPlan, ...]
    excluded_ids: tuple[str, ...]
    reference_ids: tuple[str, ...]
    reasons: tuple[str, ...]


def _money(value: int | None) -> bool:
    """Only a non-negative safe integer is money. bool is not an int here."""
    return type(value) is int and value >= 0


def _pass(key: str, label: str, basis: str, detail: str) -> Condition:
    return Condition(key, label, "pass", basis, detail)


def _fail(key: str, label: str, basis: str, detail: str) -> Condition:
    return Condition(key, label, "fail", basis, detail)


def _unknown(key: str, label: str, basis: str, detail: str) -> Condition:
    return Condition(key, label, "unknown", basis, detail)


def overnight_open_verdict(
    *,
    open_minutes: int | None,
    close_minutes: int | None,
    visit: datetime | None,
    crosses_midnight: bool | None,
    visit_is_next_day: bool | None,
) -> OpeningVerdict:
    """AC24: 01:30 after midnight is not the same as 01:30 on the opening day.

    All inputs are explicit: nothing reads the system clock. A venue open
    until 02:00 is only open at 01:30 for a visit on the *next* calendar day.
    Any missing input returns ``unknown`` rather than promising business hours.
    """
    if (
        not _money(open_minutes)
        or not _money(close_minutes)
        or visit is None
        or crosses_midnight is None
        or visit_is_next_day is None
    ):
        return "unknown"
    if not 0 <= open_minutes <= 1440 or not 0 <= close_minutes <= 1440:
        return "unknown"
    current = visit.hour * 60 + visit.minute
    if crosses_midnight:
        if visit_is_next_day:
            inside = current <= close_minutes
        else:
            inside = current >= open_minutes
    else:
        if close_minutes < open_minutes:
            return "unknown"
        inside = open_minutes <= current <= close_minutes
    return "open" if inside else "closed"


def _stay_band(plan: PlanInput) -> Condition:
    value = plan.per_room_night_fen
    if not _money(value):
        return _unknown(
            "stay_band", "住宿价格区间", "per_room_night_missing",
            "住宿每房每晚已知费用未取得，无法判断 100~200 元选入区间。",
        )
    if value < STAY_MIN_FEN:
        return _fail(
            "stay_band", "住宿价格区间", "below_selection_band",
            f"每房每晚 {value} 分低于 ¥100 选入下限，不进入本轮候选。",
        )
    if value > STAY_MAX_FEN:
        return _fail(
            "stay_band", "住宿价格区间", "above_selection_band",
            f"每房每晚 {value} 分高于 ¥200 选入上限；低车费或高评分不能抵消（AC12）。",
        )
    return _pass(
        "stay_band", "住宿价格区间", "inside_selection_band",
        f"每房每晚 {value} 分在 100~200 元选入区间内；这只是选入前提，不是可订结论。",
    )


def _walk_or_drive(plan: PlanInput) -> tuple[Condition, bool]:
    walk = plan.walk_meters
    drive = plan.drive_seconds
    walk_known = _money(walk)
    drive_known = _money(drive)
    walk_pass = walk_known and walk <= WALK_MAX_METERS
    drive_pass = drive_known and drive <= DRIVE_MAX_SECONDS
    if walk_pass:
        return _pass(
            "transport", "交通子指标", "walk_distance",
            f"步行实际路线 {walk} 米 ≤1500 米，满足；以距离为准，步行时间不替代距离。",
        ), True
    if drive_pass:
        return _pass(
            "transport", "交通子指标", "drive_duration",
            f"驾车预计 {drive} 秒 ≤900 秒，满足；不把步行距离换算成驾车时间。",
        ), False
    if walk_known and drive_known:
        time_hint = (
            f"即使步行预计只有 {plan.walk_duration_seconds} 秒，也不能用时间替代距离。"
            if _money(plan.walk_duration_seconds)
            else "步行以实际距离为准，不能用步行时间替代距离。"
        )
        return _fail(
            "transport", "交通子指标", "both_modes_over_limit",
            f"步行 {walk} 米与驾车 {drive} 秒均超限，交通子指标不通过；"
            + time_hint + "（AC05/AC06）。",
        ), False
    return _unknown(
        "transport", "交通子指标", "route_evidence_incomplete",
        "步行或驾车证据不完整，缺失不按超限也不按通过处理。",
    ), False


def _fare_gate(plan: PlanInput, walking_pass: bool) -> tuple[Condition, int | None, tuple[str, ...]]:
    """Return (condition, fare used in the total, extra notes)."""
    state = plan.fare_state
    lower = plan.fare_lower_fen
    upper = plan.fare_upper_fen
    notes: list[str] = []

    if walking_pass:
        notes.append("步行达标，返程消费按 0 计；车费估价即使存在也只作参考（R08）。")
        return _pass(
            "fare_gate", "返程车费", "walk_zero_return",
            "步行路线达标，返程消费可为零，不再用车费门槛排除本方案。",
        ), 0, tuple(notes)

    if state in ("unknown", "expired"):
        reason = "车费估价未取得" if state == "unknown" else "车费估价已过期"
        notes.append(f"{reason}；另有 1 项未核实返程费用未计入已知费用小计。")
        return _unknown(
            "fare_gate", "返程车费", state,
            f"{reason}，不按 0 也不按区间下界放行（AC10）。",
        ), None, tuple(notes)

    if not _money(lower) or not _money(upper) or lower > upper:
        notes.append("车费区间字段不完整，另有 1 项未核实返程费用未计入。")
        return _unknown(
            "fare_gate", "返程车费", "fare_shape_invalid",
            "来源车费区间不完整，不猜测金额、不按下界放行。",
        ), None, tuple(notes)

    # A range is judged by its UPPER bound, never its lower bound (AC10).
    if upper > FARE_GATE_FEN:
        detail = (
            f"车费区间上界 {upper} 分超过 {FARE_GATE_FEN} 分门槛；"
            "不按区间下界放行（AC10）。"
        )
        if upper <= FARE_OBSERVABLE_MAX_FEN:
            detail += "该金额仍在 0~16 元可观察范围内，可显示但不满足车费门槛。"
        notes.append("车费不满足门槛，本方案不进入总成本排名。")
        return _fail("fare_gate", "返程车费", "upper_bound_over_gate", detail), None, tuple(notes)

    used = upper
    if state == "range":
        notes.append(f"车费为区间，按上界 {used} 分保守计入已知费用小计。")
    return _pass(
        "fare_gate", "返程车费", "upper_bound_within_gate",
        f"车费上界 {used} 分 ≤{FARE_GATE_FEN} 分门槛，属于 0~16 元可观察范围。",
    ), used, tuple(notes)


def _night_access(plan: PlanInput) -> Condition:
    if plan.night_access_verified and plan.future_opening_verified:
        return _pass(
            "night_access", "夜间可达", "source_verified",
            "来源已核实夜间入口与未来到访时段营业。",
        )
    return _unknown(
        "night_access", "夜间可达", "night_entrance_unverified",
        "夜间入口、跨午夜营业与未来时段均未核实；不承诺可用，当前估价不是未来深夜实价。",
    )


def _subway(plan: PlanInput) -> Condition:
    if not plan.subway_required:
        return _pass(
            "subway_requirement", "地铁要求", "preference_only",
            "地铁仅为偏好，不作为硬排除条件（AC25）。",
        )
    if plan.subway_within_limit is True:
        return _pass(
            "subway_requirement", "地铁要求", "verified_within_limit",
            "已按要求核实地铁步行时间在限定范围内。",
        )
    if plan.subway_within_limit is False:
        return _fail(
            "subway_requirement", "地铁要求", "verified_over_limit",
            "已明确超出要求范围，修改条件后重新筛选对应路线。",
        )
    return _unknown(
        "subway_requirement", "地铁要求", "subway_unverified",
        "地铁已成为硬条件但尚无核实证据，不按满足处理。",
    )


def _fare_eligibility(plan: PlanInput, walking_pass: bool) -> Condition:
    if walking_pass:
        return _pass(
            "fare_eligibility", "车型与优惠资格", "walk_no_fare_needed",
            "本方案步行达标、返程消费按 0 计，不依赖拼车／企业／会员车费。",
        )
    if plan.fare_ordinary_verified:
        return _pass(
            "fare_eligibility", "车型与优惠资格", "verified_ordinary_fare",
            "已核实普通可用车型与优惠适用资格（AC33）。",
        )
    return _unknown(
        "fare_eligibility", "车型与优惠资格", "ordinary_fare_unverified",
        "普通可用车型容量、拼车／企业／未验证会员优惠资格未核实，不默认可用（AC33）。",
    )


def _upstream_gate(plan: PlanInput) -> Condition:
    if plan.upstream_qualified:
        return _pass(
            "upstream_gate", "P6 资格前置", "p6_qualified",
            "该报价在 P6 资格与费用引擎中为合格档，允许进入 P7 硬筛。",
        )
    return _fail(
        "upstream_gate", "P6 资格前置", "p6_not_qualified",
        "该报价在 P6 未达到合格档（参考或排除），不进入 P7 硬筛与排序。",
    )


def _ranking_fields(plan: PlanInput) -> Condition:
    score = plan.guest_score_tenths
    count = plan.review_count
    if _money(score) and 0 <= score <= 50 and _money(count):
        return _pass(
            "ranking_fields", "排序字段", "both_present",
            "5 分制住客评分与评价人数均已取得，可参与本轮排序。",
        )
    missing = []
    if not (_money(score) and 0 <= score <= 50):
        missing.append("住客评分")
    if not _money(count):
        missing.append("评价人数")
    return _unknown(
        "ranking_fields", "排序字段", "ranking_fields_missing",
        "、".join(missing) + "未取得；本方案不可比较，不进入排名，也不按总成本回退排序。"
        "酒店星级不能充当住客评分（R04）。",
    )


def assess(plan: PlanInput) -> PlanAssessment:
    """Deterministic three-state hard gate + known-cost subtotal for one plan."""
    stay = _stay_band(plan)
    transport, walking_pass = _walk_or_drive(plan)
    fare, fare_used, fare_notes = _fare_gate(plan, walking_pass)
    night = _night_access(plan)
    subway = _subway(plan)
    ranking_fields = _ranking_fields(plan)
    conditions = (
        _upstream_gate(plan), stay, transport, fare, _fare_eligibility(plan, walking_pass),
        night, subway, ranking_fields,
    )
    verdicts = [row.verdict for row in conditions]
    if "fail" in verdicts:
        tier: Tier = "excluded"
    elif "unknown" in verdicts:
        tier = "reference_only"
    else:
        tier = "rankable"

    notes: list[str] = list(fare_notes)
    stay_known = plan.stay_known_fen if _money(plan.stay_known_fen) else None
    if stay_known is None:
        notes.append("住宿已知费用小计未取得，本方案不计算综合总成本。")
        total = None
    elif fare_used is None:
        notes.append("合计仅为住宿已知费用小计；另有 1 项未核实返程费用未计入。")
        total = stay_known
    else:
        total = stay_known + fare_used
        notes.append(
            f"已知费用小计 {total} 分 = 住宿 {stay_known} 分 + 1 次返程 {fare_used} 分；"
            "不自动按晚数增加返程次数，不加入未请求的去程与餐饮（AC31）。"
        )
    if tier == "excluded":
        notes.append("存在不通过条件，只作排除展示，不进入总成本排名。")
    elif tier == "reference_only":
        notes.append("存在未核实项，只作参考展示，不进入总成本排名。")

    return PlanAssessment(
        plan_id=plan.plan_id,
        tier=tier,
        conditions=conditions,
        stay_known_fen=stay_known,
        fare_used_fen=fare_used,
        total_known_fen=total,
        guest_score_tenths=plan.guest_score_tenths,
        review_count=plan.review_count,
        notes=tuple(notes),
        risks=RISK_NOTES,
    )


def rank(plans: tuple[PlanInput, ...] | list[PlanInput]) -> RankingResult:
    """Rank by guest score -> review count -> known subtotal -> input order.

    There is deliberately NO fallback to a total-cost order. If no plan has
    both ranking fields, ``status`` is ``not_comparable`` and ``order`` is
    empty; the caller must show the reasons instead of a fabricated ranking.
    """
    assessments = [assess(plan) for plan in plans]
    excluded = tuple(row.plan_id for row in assessments if row.tier == "excluded")
    reference = tuple(row.plan_id for row in assessments if row.tier == "reference_only")
    rankable = [row for row in assessments if row.tier == "rankable"]
    if not rankable:
        reasons = [
            "排序主键为住客评分 → 评价人数，本轮没有同时取得这两项且通过全部硬条件的方案。",
            "住客评分与评价人数当前来源均未提供；不按总成本回退排序，也不用酒店星级替代。",
            "这是一条外部字段阻断，不是「全城无合适住宿」。",
        ]
        if excluded:
            reasons.append(f"另有 {len(excluded)} 个方案因硬条件不通过被排除。")
        if reference:
            reasons.append(f"另有 {len(reference)} 个方案因存在未核实项只作参考展示。")
        return RankingResult("not_comparable", (), excluded, reference, tuple(reasons))

    keyed = []
    for index, row in enumerate(rankable):
        assert row.guest_score_tenths is not None and row.review_count is not None
        keyed.append((
            -row.guest_score_tenths,
            -row.review_count,
            row.total_known_fen if row.total_known_fen is not None else 0,
            index,
            row,
        ))
    keyed.sort(key=lambda item: item[:4])
    order = tuple(
        RankedPlan(
            plan_id=item[4].plan_id,
            position=position,
            guest_score_tenths=item[4].guest_score_tenths,  # type: ignore[arg-type]
            review_count=item[4].review_count,  # type: ignore[arg-type]
            total_known_fen=item[4].total_known_fen,
            tie_break="评分 → 评价人数 → 已知费用小计 → 输入顺序",
        )
        for position, item in enumerate(keyed, start=1)
    )
    return RankingResult("ranked", order, excluded, reference, (
        "排序主键：住客评分降序 → 评价人数降序 → 已知费用小计升序 → 输入顺序。",
        "参考层排序，不代表可订；已知费用小计不含未核实费用。",
    ))
