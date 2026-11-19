"""P7 acceptance: return-trip hard gate, known-cost subtotal and ranking.

Every test maps to an acceptance row (AC05-AC12, AC23-AC25, AC31, AC33) or to
one of the honesty invariants. The point is not "it runs": it is that a rule
which quietly orders by total cost, treats an unknown fare as zero, borrows a
star rating as a guest score, or lets a short walking time replace distance,
fails loudly.
"""

from datetime import date, datetime, timedelta

import pytest

from stay_agent import return_trip as r
from stay_agent.contracts import (
    BudgetAssessmentWire,
    FeeBreakdownWire,
    Qualification,
    QuotesResponse,
    RoomQuote,
    SearchInput,
    TravelRouteResponse,
    WalkingEvidence,
)
from stay_agent.decision import build_quote_decision, build_route_assessment


def base(**overrides):
    """A plan that can reach ``rankable``: every field is supplied."""
    values = dict(
        plan_id="p1",
        per_room_night_fen=16900,
        stay_known_fen=16900,
        fare_lower_fen=1200,
        fare_upper_fen=1200,
        fare_state="known",
        walk_meters=1600,
        drive_seconds=800,
        guest_score_tenths=46,
        review_count=900,
        night_access_verified=True,
        future_opening_verified=True,
        fare_ordinary_verified=True,
    )
    values.update(overrides)
    return r.PlanInput(**values)


def run(**overrides):
    return r.assess(base(**overrides))


def verdicts(assessment):
    return assessment.verdicts


# --- AC05 / AC06: walking distance OR driving duration ---------------------

def test_ac05_walk_1500_and_drive_900_boundaries_pass():
    assert verdicts(run(walk_meters=1500, drive_seconds=None))["transport"] == "pass"
    assert verdicts(run(walk_meters=None, drive_seconds=900))["transport"] == "pass"


def test_ac05_walk_1501_and_drive_901_both_over_fail():
    assessment = run(walk_meters=1501, drive_seconds=901)
    assert verdicts(assessment)["transport"] == "fail"
    assert assessment.tier == "excluded"


def test_ac06_short_walking_time_never_replaces_distance():
    assessment = run(walk_meters=1600, walk_duration_seconds=540, drive_seconds=901)
    condition = next(c for c in assessment.conditions if c.key == "transport")
    assert condition.verdict == "fail"
    assert "距离" in condition.detail


def test_ac06_walk_1400_with_20_minutes_still_passes_by_distance():
    assessment = run(walk_meters=1400, walk_duration_seconds=1200, drive_seconds=None)
    condition = next(c for c in assessment.conditions if c.key == "transport")
    assert condition.verdict == "pass"
    assert condition.basis == "walk_distance"


def test_missing_one_route_evidence_is_unknown_not_fail():
    assert verdicts(run(walk_meters=1600, drive_seconds=None))["transport"] == "unknown"


# --- AC07 / AC08 / AC10 / AC33: fare gate ----------------------------------

def test_ac07_fare_1500_passes_and_1501_fails():
    assert verdicts(run(fare_lower_fen=1500, fare_upper_fen=1500))["fare_gate"] == "pass"
    assert verdicts(run(fare_lower_fen=1501, fare_upper_fen=1501))["fare_gate"] == "fail"


def test_ac08_14_yuan_passes_20_yuan_fails_without_a_distance_gate():
    assert verdicts(run(fare_lower_fen=1400, fare_upper_fen=1400))["fare_gate"] == "pass"
    assert verdicts(run(fare_lower_fen=2000, fare_upper_fen=2000))["fare_gate"] == "fail"
    assert not hasattr(r, "DISTANCE_MAX_METERS")


def test_fare_between_1500_and_1600_is_visible_but_fails_the_gate():
    condition = next(
        c for c in run(fare_lower_fen=1550, fare_upper_fen=1550).conditions
        if c.key == "fare_gate"
    )
    assert condition.verdict == "fail"
    assert "可观察范围" in condition.detail
    assert r.FARE_GATE_FEN == 1500
    assert r.FARE_OBSERVABLE_MAX_FEN == 1600


@pytest.mark.parametrize("state", ["unknown", "expired"])
def test_ac10_unknown_or_expired_fare_never_passes_and_is_never_zero(state):
    assessment = run(fare_state=state, fare_lower_fen=None, fare_upper_fen=None)
    assert verdicts(assessment)["fare_gate"] == "unknown"
    assert assessment.fare_used_fen is None
    assert assessment.total_known_fen == 16900
    assert any("未计入" in note for note in assessment.notes)


def test_ac10_range_upper_bound_over_gate_never_uses_lower_bound():
    assessment = run(fare_state="range", fare_lower_fen=1200, fare_upper_fen=1800)
    assert verdicts(assessment)["fare_gate"] == "fail"
    assert "区间下界" in next(
        c.detail for c in assessment.conditions if c.key == "fare_gate"
    )


def test_range_within_gate_uses_conservative_upper_bound():
    assessment = run(fare_state="range", fare_lower_fen=1200, fare_upper_fen=1400)
    assert verdicts(assessment)["fare_gate"] == "pass"
    assert assessment.fare_used_fen == 1400
    assert assessment.total_known_fen == 18300


def test_ac33_unverified_ordinary_fare_never_counts():
    assessment = run(fare_ordinary_verified=False)
    assert verdicts(assessment)["fare_eligibility"] == "unknown"
    assert assessment.tier == "reference_only"


def test_walking_plan_does_not_need_a_verified_fare():
    assessment = run(walk_meters=1400, fare_state="unknown", fare_lower_fen=None,
                     fare_upper_fen=None, fare_ordinary_verified=False)
    assert verdicts(assessment)["fare_gate"] == "pass"
    assert assessment.fare_used_fen == 0
    assert assessment.total_known_fen == 16900


# --- AC09 / AC24 / AC25: time semantics ------------------------------------

def test_ac09_current_fare_is_never_a_future_late_night_price():
    assessment = run(night_access_verified=False, future_opening_verified=False)
    assert verdicts(assessment)["night_access"] == "unknown"
    assert any("当前估价不等于未来深夜实价" in risk for risk in assessment.risks)


def test_ac24_overnight_0130_next_day_is_open():
    assert r.overnight_open_verdict(
        open_minutes=600, close_minutes=120,
        visit=datetime(2026, 11, 8, 1, 30),
        crosses_midnight=True, visit_is_next_day=True,
    ) == "open"


def test_ac24_overnight_0130_same_day_is_not_the_next_morning():
    assert r.overnight_open_verdict(
        open_minutes=600, close_minutes=120,
        visit=datetime(2026, 11, 7, 1, 30),
        crosses_midnight=True, visit_is_next_day=False,
    ) == "closed"


def test_ac24_known_2300_closing_rejects_midnight_visit():
    assert r.overnight_open_verdict(
        open_minutes=600, close_minutes=1380,
        visit=datetime(2026, 11, 8, 0, 30),
        crosses_midnight=False, visit_is_next_day=False,
    ) == "closed"


def test_ac24_missing_opening_evidence_is_unknown():
    assert r.overnight_open_verdict(
        open_minutes=None, close_minutes=120,
        visit=datetime(2026, 11, 8, 1, 30),
        crosses_midnight=True, visit_is_next_day=True,
    ) == "unknown"


def test_ac25_subway_preference_then_hard_condition():
    assert verdicts(run(subway_required=False))["subway_requirement"] == "pass"
    assert verdicts(run(subway_required=True, subway_within_limit=None))["subway_requirement"] == "unknown"
    assert verdicts(run(subway_required=True, subway_within_limit=False))["subway_requirement"] == "fail"
    assert verdicts(run(subway_required=True, subway_within_limit=True))["subway_requirement"] == "pass"


# --- AC11 / AC12 / AC13 / AC31: money and the user's ranking order ---------

def test_ac11_known_subtotals_are_recomputable():
    alpha = run(plan_id="A", stay_known_fen=16900, per_room_night_fen=16900,
                fare_lower_fen=1200, fare_upper_fen=1200)
    beta = run(plan_id="B", stay_known_fen=18800, per_room_night_fen=18800,
               walk_meters=1400, drive_seconds=None, fare_state="unknown",
               fare_lower_fen=None, fare_upper_fen=None)
    assert alpha.total_known_fen == 18100
    assert beta.total_known_fen == 18800


def test_ac11_ranking_uses_guest_score_not_total_cost():
    alpha = base(plan_id="A", stay_known_fen=16900, guest_score_tenths=46, review_count=900)
    beta = base(plan_id="B", stay_known_fen=18800, per_room_night_fen=18800,
                walk_meters=1400, drive_seconds=None, fare_state="unknown",
                fare_lower_fen=None, fare_upper_fen=None,
                guest_score_tenths=48, review_count=700)
    result = r.rank([alpha, beta])
    assert result.status == "ranked"
    assert [row.plan_id for row in result.order] == ["B", "A"]
    assert result.order[0].guest_score_tenths == 48


def test_review_count_is_the_second_key_and_input_order_the_last():
    first = base(plan_id="first", guest_score_tenths=46, review_count=900)
    second = base(plan_id="second", guest_score_tenths=46, review_count=1200)
    third = base(plan_id="third", guest_score_tenths=46, review_count=900)
    result = r.rank([first, second, third])
    assert [row.plan_id for row in result.order] == ["second", "first", "third"]


def test_known_subtotal_breaks_a_full_tie():
    cheap = base(plan_id="cheap", stay_known_fen=15000, per_room_night_fen=15000)
    dear = base(plan_id="dear", stay_known_fen=19000, per_room_night_fen=19000)
    result = r.rank([dear, cheap])
    assert [row.plan_id for row in result.order] == ["cheap", "dear"]


def test_ac12_stay_over_200_is_never_offset_by_a_zero_fare():
    assessment = run(per_room_night_fen=20500, stay_known_fen=20500,
                     walk_meters=1400, drive_seconds=None)
    assert assessment.tier == "excluded"
    assert verdicts(assessment)["stay_band"] == "fail"
    assert "不能抵消" in next(c.detail for c in assessment.conditions if c.key == "stay_band")


@pytest.mark.parametrize("value,expected", [
    (9900, "fail"), (10000, "pass"), (20000, "pass"), (20001, "fail"),
])
def test_ac13_selection_band_boundaries(value, expected):
    assert verdicts(run(per_room_night_fen=value, stay_known_fen=value))["stay_band"] == expected


def test_missing_stay_price_is_unknown_not_out_of_band():
    assert verdicts(run(per_room_night_fen=None))["stay_band"] == "unknown"


def test_ac31_two_nights_one_return_372_and_no_auto_double():
    assessment = run(stay_known_fen=36000, per_room_night_fen=18000,
                     fare_lower_fen=1200, fare_upper_fen=1200)
    assert assessment.total_known_fen == 37200
    assert any("不自动按晚数增加返程次数" in note for note in assessment.notes)


def test_unknown_stay_amount_means_no_total():
    assessment = run(stay_known_fen=None)
    assert assessment.total_known_fen is None
    assert any("不计算综合总成本" in note for note in assessment.notes)


def test_bool_is_never_money():
    assessment = run(stay_known_fen=True)  # type: ignore[arg-type]
    assert assessment.stay_known_fen is None


# --- Ranking honesty --------------------------------------------------------

def test_partial_ranking_fields_only_the_complete_plan_is_ranked():
    result = r.rank([base(plan_id="a", guest_score_tenths=None),
                     base(plan_id="b", guest_score_tenths=48)])
    assert result.status == "ranked"
    assert [row.plan_id for row in result.order] == ["b"]
    assert "a" in result.reference_ids


def test_no_ranking_fields_produces_not_comparable_and_no_order():
    result = r.rank([base(plan_id="a", guest_score_tenths=None, review_count=None),
                     base(plan_id="b", guest_score_tenths=None, review_count=None)])
    assert result.status == "not_comparable"
    assert result.order == ()
    assert any("不按总成本回退排序" in reason for reason in result.reasons)


def test_star_rating_is_never_substituted_for_guest_score():
    assessment = run(guest_score_tenths=None, review_count=900)
    detail = next(c.detail for c in assessment.conditions if c.key == "ranking_fields")
    assert verdicts(assessment)["ranking_fields"] == "unknown"
    assert "酒店星级不能充当住客评分" in detail


def test_p6_reference_quote_never_reaches_p7_ranking():
    assessment = run(upstream_qualified=False)
    assert assessment.tier == "excluded"
    assert verdicts(assessment)["upstream_gate"] == "fail"


def test_ac23_branch_options_are_preserved_not_merged():
    alpha = base(plan_id="branch-a", branch_label="甲店", guest_score_tenths=48)
    beta = base(plan_id="branch-b", branch_label="乙店", guest_score_tenths=46)
    result = r.rank([alpha, beta])
    assert [row.plan_id for row in result.order] == ["branch-a", "branch-b"]
    assert len(result.order) == 2


def test_risks_always_travel_with_the_result():
    assessment = run()
    assert len(assessment.risks) >= 4
    assert any("不代表可订" in risk for risk in assessment.risks)


# --- Wiring: each response only ever uses its own evidence ------------------

def _context():
    return SearchInput(
        place="广州", check_in=date.today() + timedelta(days=1),
        check_out=date.today() + timedelta(days=2),
    )


def _qualification():
    return Qualification(
        tier="qualified",
        fees=FeeBreakdownWire(
            nights=1, rooms=1, total_known_fen=16900,
            per_room_night_fen=16900, tax_basis="source_states_tax_included",
        ),
        budget=BudgetAssessmentWire(
            verdict="pass", basis="no_budget", detail="本次未设置预算门槛。",
        ),
    )


def test_quote_decision_never_invents_route_or_score_evidence():
    response = QuotesResponse(
        request_id="r1", hotel_id=1, hotel_name="测试住宿", status="complete",
        context=_context(), queried_at="2026-11-07T00:00:00+08:00",
        quotes=[RoomQuote(id="q1", room_name="测试房", qualification=_qualification())],
    )
    decision = build_quote_decision(response)
    assert decision.status == "not_comparable"
    assert decision.order == []
    assert "不跨响应拼接" in decision.evidence_binding
    row = decision.assessments[0]
    conditions = {condition.key: condition.verdict for condition in row.conditions}
    assert conditions["night_access"] == "unknown"
    assert conditions["ranking_fields"] == "unknown"
    assert conditions["transport"] == "unknown"


def test_route_assessment_never_invents_a_stay_price():
    response = TravelRouteResponse(
        request_id="r1", hotel_id=1, provider="amap", status="complete",
        queried_at="2026-11-07T00:00:00+08:00", hotel_name="测试住宿",
        restaurant_name="测试门店", hotel_match_basis="exact_fields",
        return_walk=WalkingEvidence(
            origin_name="测试门店", destination_name="测试住宿",
            distance_meters=1400, duration_seconds=900,
        ),
    )
    row = build_route_assessment(response)
    conditions = {condition.key: condition.verdict for condition in row.conditions}
    assert conditions["stay_band"] == "unknown"
    assert conditions["transport"] == "pass"
    assert conditions["night_access"] == "unknown"
    assert row.total_known_fen is None


# --- Branch edges that must not silently become "pass" ----------------------

def test_overnight_invalid_minute_range_is_unknown():
    assert r.overnight_open_verdict(
        open_minutes=1500, close_minutes=120,
        visit=datetime(2026, 11, 8, 1, 30),
        crosses_midnight=True, visit_is_next_day=True,
    ) == "unknown"


def test_overnight_non_crossing_close_before_open_is_unknown():
    assert r.overnight_open_verdict(
        open_minutes=1380, close_minutes=120,
        visit=datetime(2026, 11, 8, 1, 30),
        crosses_midnight=False, visit_is_next_day=False,
    ) == "unknown"


def test_malformed_fare_range_is_unknown_not_a_guessed_amount():
    assessment = run(fare_state="range", fare_lower_fen=1800, fare_upper_fen=1200)
    assert verdicts(assessment)["fare_gate"] == "unknown"
    assert assessment.fare_used_fen is None


def test_only_review_count_missing_names_exactly_that_field():
    assessment = run(guest_score_tenths=46, review_count=None)
    detail = next(c.detail for c in assessment.conditions if c.key == "ranking_fields")
    assert "评价人数未取得" in detail
    assert "住客评分未取得" not in detail


def test_not_comparable_reasons_count_excluded_and_reference_groups():
    excluded = r.rank([base(plan_id="e", per_room_night_fen=20500, stay_known_fen=20500)])
    assert excluded.status == "not_comparable"
    assert any("硬条件不通过被排除" in reason for reason in excluded.reasons)
    reference = r.rank([base(plan_id="r", guest_score_tenths=None)])
    assert reference.status == "not_comparable"
    assert any("只作参考展示" in reason for reason in reference.reasons)
