"""P6 acceptance: deterministic qualification and fee engine.

Every test here maps to one acceptance row (AC12-AC22, AC32) or to one of the
four invariants the engine promises. The point of the suite is not "it runs":
it is that a rule which quietly turns unknown into zero, or doubles a single
room's stock, fails loudly.
"""

import pytest

from stay_agent import pricing as p


def base(**overrides):
    """A one-night, one-room, two-adult tuniu-like quote."""
    values = dict(
        source_id="tuniu",
        room_name="独卫大床房",
        amount_fen=18000,
        price_unit="per_room_night",
        remaining=5,
        nights=1,
        rooms=1,
        adults=2,
    )
    values.update(overrides)
    return p.QuoteInput(**values)


def run(**overrides):
    return p.assess(base(**overrides))


def verdicts(assessment):
    return {row.condition: row.verdict for row in assessment.conditions}


# --- AC12: a cheap total never overrides a failed nightly budget ------------

def test_ac12_a_lower_total_never_cancels_a_failed_nightly_budget():
    assessment = run(amount_fen=20500, budget_fen=20000)
    assert assessment.tier == "excluded"
    assert assessment.budget.verdict == "fail"
    assert any("预算" in reason for reason in assessment.reasons)


def test_ac12_budget_failure_is_reported_even_when_every_condition_passes():
    assessment = run(amount_fen=20500, budget_fen=20000)
    assert all(row.verdict == "pass" for row in assessment.conditions)
    assert assessment.tier == "excluded"


# --- AC13: the cheapest *qualified* quote wins ------------------------------

def test_ac13_lowest_qualified_skips_a_cheaper_excluded_quote():
    shared = p.assess(base(room_name="公卫单人间", amount_fen=13900))
    twin = p.assess(base(room_name="独卫双床房", amount_fen=16900))
    king = p.assess(base(room_name="独卫大床房", amount_fen=18900))
    assert shared.tier == "excluded"
    assert (twin.tier, king.tier) == ("qualified", "qualified")
    assert p.lowest_qualified((shared, twin, king)) == 1


def test_ac13_lowest_qualified_never_falls_back_to_a_reference_quote():
    unknown_stock = p.assess(base(remaining=None, on_request=None, amount_fen=13900))
    assert unknown_stock.tier == "reference"
    assert p.lowest_qualified((unknown_stock,)) is None


def test_ac13_ranking_is_per_room_night_so_more_nights_cannot_win():
    one_night = p.assess(base(amount_fen=18000, nights=1))
    three_nights = p.assess(base(amount_fen=17000, nights=3))
    assert one_night.fees.per_room_night_fen == 18000
    assert three_nights.fees.per_room_night_fen == 17000
    assert p.lowest_qualified((one_night, three_nights)) == 1
    # A quote whose amount is unknown can never be the cheapest.
    missing = p.assess(base(amount_fen=None))
    assert p.lowest_qualified((one_night, missing)) == 0


# --- AC14 / AC15: room form and bathroom -----------------------------------

def test_ac14_a_dormitory_bed_is_excluded_by_default():
    assessment = run(room_name="青旅四人床位房")
    assert assessment.tier == "excluded"
    assert verdicts(assessment)["room_form"] == "fail"
    assert verdicts(assessment)["private_bathroom"] == "fail"


def test_ac15_a_present_room_name_passes_only_as_a_domestic_default():
    assessment = run(room_name="独卫大床房")
    row = next(r for r in assessment.conditions if r.condition == "room_form")
    assert row.verdict == "pass"
    assert row.presumed is True
    assert row.basis == "domestic_default_whole_room"
    # The presumption is carried by ``presumed`` (the page prints 推定 from it),
    # not by the sentence, so the wire never has to be parsed for wording.
    assert row.presumed is True


def test_ac15_a_missing_room_name_cannot_borrow_the_whole_room_default():
    """r64's rule, one layer down: no name, no default. Fail, never guess."""
    assessment = run(room_name=None)
    row = next(r for r in assessment.conditions if r.condition == "room_form")
    assert row.verdict == "fail"
    assert row.presumed is False
    assert row.basis == "room_name_missing"
    assert assessment.tier == "excluded"


def test_ac15_an_explicit_shared_bathroom_room_is_not_presumed():
    assessment = run(room_name="标准间（公共卫浴）")
    assert verdicts(assessment)["private_bathroom"] == "fail"
    assert assessment.tier == "excluded"


# --- AC16: mandatory fees, deposit and "not verified" ----------------------

def test_ac16_mandatory_fee_is_counted_and_can_break_the_budget():
    assessment = run(
        amount_fen=18000,
        budget_fen=20000,
        mandatory_fees=(p.FeeInput("到店清洁费", 3000, "room_night", verified=True),),
    )
    assert assessment.fees.room_charge_fen == 18000
    assert assessment.fees.mandatory_fees_fen == 3000
    assert assessment.fees.total_known_fen == 21000
    assert assessment.fees.per_room_night_fen == 21000
    assert assessment.tier == "excluded"


def test_ac16_a_refundable_deposit_is_listed_but_never_consumption():
    assessment = run(deposit=p.DepositInput(20000, refundable=True))
    assert assessment.fees.total_known_fen == 18000
    assert assessment.fees.deposit_fen == 20000
    deposit_line = next(line for line in assessment.fees.lines if line.kind == "deposit")
    assert deposit_line.counted is False
    assert assessment.tier == "qualified"


def test_ac16_a_non_refundable_deposit_is_consumption():
    assessment = run(deposit=p.DepositInput(5000, refundable=False))
    assert assessment.fees.total_known_fen == 23000
    assert assessment.fees.mandatory_fees_fen == 5000


def test_ac16_an_unverified_deposit_is_unknown_not_zero():
    assessment = run(deposit=p.DepositInput(20000))
    assert assessment.fees.total_known_fen == 18000
    assert assessment.fees.complete is False
    assert assessment.fees.deposit_refundable is None
    assert any("押金" in item for item in assessment.fees.unknown_items)


def test_ac16_an_unverified_fee_is_listed_but_excluded_from_the_sum():
    assessment = run(
        mandatory_fees=(p.FeeInput("度假村费", 5000, "room_night", verified=False),),
        budget_fen=20000,
    )
    line = next(line for line in assessment.fees.lines if line.kind == "mandatory_fee")
    assert line.counted is False
    assert assessment.fees.total_known_fen == 18000
    assert assessment.fees.complete is False
    # It passed the budget, but the wording must admit what is missing.
    assert assessment.budget.verdict == "pass"
    assert "未核实费用未计入" in assessment.budget.detail


# --- AC17: a discount without verified eligibility never cuts the total ----

def test_ac17_an_unverified_discount_is_potential_only():
    assessment = run(
        amount_fen=21900,
        budget_fen=20000,
        discounts=(p.DiscountInput("潜在券", 3000, eligible=None),),
    )
    assert assessment.fees.discount_fen == 3000
    assert assessment.fees.discount_applied_fen == 0
    assert assessment.fees.total_known_fen == 21900
    assert assessment.budget.verdict == "fail"
    assert assessment.tier == "excluded"


def test_ac17_a_verified_discount_reduces_the_total_once():
    assessment = run(
        amount_fen=21900,
        budget_fen=20000,
        discounts=(p.DiscountInput("会员券", 3000, eligible=True),),
    )
    assert assessment.fees.total_known_fen == 18900
    assert assessment.budget.verdict == "pass"
    assert assessment.tier == "qualified"


# --- AC18: nightly prices, averaged prices and stock -----------------------

def test_ac18_a_worst_night_above_budget_fails_even_if_the_average_fits():
    assessment = run(nights=2, nightly_fen=(19000, 21000), budget_fen=20000)
    assert assessment.budget.basis == "nightly_verified"
    assert assessment.budget.verdict == "fail"
    assert assessment.tier == "excluded"


def test_ac18_a_complete_nightly_series_that_fits_passes_on_the_worst_night():
    assessment = run(nights=2, nightly_fen=(18000, 19500), budget_fen=20000)
    assert assessment.budget.basis == "nightly_verified"
    assert assessment.budget.verdict == "pass"
    assert assessment.budget.compared_fen == 19500


def test_ac18_an_averaged_multi_night_price_passes_but_always_carries_the_risk():
    assessment = run(nights=3, amount_fen=18000, budget_fen=20000)
    assert assessment.budget.basis == "average_presumed"
    assert assessment.budget.verdict == "pass"
    assert "无法排除其中一晚超预算" in assessment.budget.detail
    # The user decision (2026-11-07) allows this to qualify, but the wording
    # must never read as a verified nightly total.
    assert assessment.tier == "qualified"
    assert "逐夜" not in assessment.fees.notes[0]


def test_ac18_an_incomplete_nightly_series_is_unknown_not_a_pass():
    assessment = run(nights=3, nightly_fen=(18000,), budget_fen=20000)
    assert assessment.budget.basis == "nightly_incomplete"
    assert assessment.budget.verdict == "unknown"
    assert assessment.tier == "reference"


def test_ac18_one_room_of_stock_cannot_be_multiplied_into_two_rooms():
    assessment = run(remaining=1, rooms=2, amount_fen=18000)
    assert verdicts(assessment)["inventory"] == "fail"
    assert assessment.tier == "excluded"
    # The failure must not secretly double the room charge either.
    assert assessment.fees.room_charge_fen == 36000


def test_ac18_enough_stock_for_both_rooms_passes():
    assessment = run(remaining=2, rooms=2)
    assert verdicts(assessment)["inventory"] == "pass"


# --- AC19: cancellation ------------------------------------------------

def test_ac19_a_non_refundable_cheaper_room_is_allowed_by_default():
    assessment = run(cancellation_text="预订后不可取消", amount_fen=16900)
    assert verdicts(assessment)["cancellation"] == "pass"
    assert assessment.tier == "qualified"


def test_ac19_requiring_free_cancel_moves_the_floor_to_the_refundable_room():
    strict_cheap = p.assess(base(cancellation_text="预订后不可取消", amount_fen=16900, require_free_cancel=True))
    strict_expensive = p.assess(base(cancellation_text="入住前1天可免费取消", amount_fen=18900, require_free_cancel=True))
    assert strict_cheap.tier == "excluded"
    assert strict_expensive.tier == "qualified"
    assert p.lowest_qualified((strict_cheap, strict_expensive)) == 1


def test_ac19_free_cancel_in_free_text_is_a_presumption_not_a_verification():
    assessment = run(cancellation_text="入住当天18:00前可免费取消", require_free_cancel=True)
    row = next(r for r in assessment.conditions if r.condition == "cancellation")
    assert row.verdict == "pass"
    assert row.presumed is True
    assert "推定" in row.detail


def test_ac19_a_structured_non_refundable_flag_beats_polite_free_text():
    assessment = run(
        cancellation_text="免费取消（详见条款）", cancellation_verified=False,
        require_free_cancel=True,
    )
    row = next(r for r in assessment.conditions if r.condition == "cancellation")
    assert row.verdict == "fail"
    assert row.presumed is False


# --- AC20: two rate plans of one hotel stay separate -----------------------

def test_ac20_two_plans_of_one_hotel_produce_two_independent_verdicts():
    with_breakfast = p.assess(base(room_name="独卫大床房", cancellation_text="含双早，不可取消", amount_fen=18900))
    no_breakfast = p.assess(base(room_name="独卫大床房", cancellation_text="无早，可免费取消", amount_fen=16900))
    assert with_breakfast is not no_breakfast
    assert with_breakfast.fees.room_charge_fen == 18900
    assert no_breakfast.fees.room_charge_fen == 16900
    assert with_breakfast.conditions != no_breakfast.conditions


# --- AC21: R04 guest score ------------------------------------------------

def test_ac21_a_verifiable_five_point_score_passes_the_threshold():
    assessment = run(guest_score=4.6, guest_score_scale="5")
    assert verdicts(assessment)["guest_score"] == "pass"


def test_ac21_a_score_below_the_threshold_fails():
    assessment = run(guest_score=4.4, guest_score_scale="5")
    assert verdicts(assessment)["guest_score"] == "fail"
    assert assessment.tier == "excluded"


def test_ac21_no_score_disables_the_threshold_and_says_so():
    assessment = run(guest_score=None)
    row = next(r for r in assessment.conditions if r.condition == "guest_score")
    assert row.verdict == "pass"
    assert row.basis == "r04_threshold_not_enabled_when_absent"
    assert "星级" in row.detail


def test_ac21_a_score_on_an_unstated_scale_is_never_converted():
    assessment = run(guest_score=4.6, guest_score_scale=None)
    row = next(r for r in assessment.conditions if r.condition == "guest_score")
    assert row.verdict == "unknown"
    assert assessment.tier == "reference"


# --- AC32: no double tax, no double discount, integer fen ------------------

def test_ac32_an_included_tax_is_not_added_again():
    assessment = run(
        tax_included=True,
        mandatory_fees=(p.FeeInput("税费", 1200, "room_night", verified=True, included=True),),
    )
    assert assessment.fees.mandatory_fees_fen == 0
    assert assessment.fees.total_known_fen == 18000
    assert assessment.fees.tax_basis == "source_states_tax_included"


def test_ac32_a_tax_line_is_dropped_when_the_source_says_the_price_includes_it():
    assessment = run(
        tax_included=True,
        mandatory_fees=(p.FeeInput("税费", 1200, "room_night", verified=True),),
    )
    assert assessment.fees.mandatory_fees_fen == 0
    assert any("避免重复计入" in note for note in assessment.fees.notes)


def test_ac32_a_domestic_price_without_a_tax_statement_is_presumed_inclusive():
    assessment = run(tax_included=None)
    assert assessment.fees.tax_basis == "domestic_default_tax_included"
    assert assessment.fees.total_known_fen == 18000


def test_ac32_an_excluded_tax_is_unknown_not_zero():
    assessment = run(tax_included=False)
    assert assessment.fees.tax_basis == "source_states_tax_excluded"
    assert assessment.fees.complete is False
    assert any("税额" in item for item in assessment.fees.unknown_items)


def test_ac32_all_money_stays_integer_fen_with_no_rounding_drift():
    assessment = run(amount_fen=10000, nights=3, rooms=2)
    assert assessment.fees.total_known_fen == 60000
    assert assessment.fees.per_room_night_fen == 10000
    # A fee that does not divide evenly must be reported, not silently rounded.
    odd = run(
        amount_fen=10000, nights=3, rooms=2,
        mandatory_fees=(p.FeeInput("清洁费", 100, "stay"),),
    )
    assert odd.fees.total_known_fen == 60100
    assert odd.fees.per_room_night_fen == 60100 // 6
    assert odd.fees.per_room_night_fen * 6 != odd.fees.total_known_fen
    assert isinstance(odd.fees.per_room_night_fen, int)
    assert any("取整" in note for note in odd.fees.notes)


def test_ac32_a_float_amount_is_refused_instead_of_rounded():
    with pytest.raises(ValueError):
        p.fee_total_fen(180.5, "room_night", nights=1, rooms=1, persons=2)
    with pytest.raises(ValueError):
        p.fee_total_fen(-1, "stay", nights=1, rooms=1, persons=2)


def test_ac32_zero_nights_or_rooms_is_a_programming_error_not_a_free_stay():
    with pytest.raises(ValueError):
        run(nights=0)
    with pytest.raises(ValueError):
        run(rooms=0)


# --- engine invariants ----------------------------------------------------

def test_the_engine_is_pure_and_repeatable():
    item = base(mandatory_fees=(p.FeeInput("清洁费", 3000, "room_night"),))
    first = p.assess(item)
    second = p.assess(item)
    assert first == second
    assert first.fees.lines == second.fees.lines


def test_every_condition_carries_a_basis_a_label_and_a_sentence():
    assessment = run(room_name=None, guest_score=None)
    assert {row.condition for row in assessment.conditions} == set(p.CONDITION_LABELS)
    for row in assessment.conditions:
        assert row.basis and row.label and row.detail
        assert row.verdict in ("pass", "fail", "unknown")


def test_a_per_stay_amount_for_two_rooms_is_flagged_not_multiplied():
    charge = p.room_charge(30000, nights=2, rooms=2, price_unit="per_stay", source_id="tuniu")
    assert charge.total_fen == 30000
    assert charge.presumed is True
    assert "偏低" in charge.note


def test_a_missing_amount_keeps_the_total_unknown_instead_of_zero():
    assessment = run(amount_fen=None, budget_fen=20000)
    assert assessment.fees.total_known_fen is None
    assert assessment.fees.per_room_night_fen is None
    assert assessment.budget.verdict == "unknown"
    assert assessment.budget.basis == "total_unknown"
    assert assessment.tier != "qualified"


def test_a_missing_amount_is_never_qualified_even_without_a_budget():
    # No budget means the budget check itself says nothing ("no_budget" passes).
    # Every condition can still pass while the number is missing, so the tier
    # must refuse to print 合格 on a card that shows no price.
    assessment = run(amount_fen=None)
    assert assessment.fees.total_known_fen is None
    assert assessment.tier == "reference"
    assert any("金额缺失" in reason for reason in assessment.reasons)


def test_a_flyai_amount_without_currency_is_never_converted():
    charge = p.room_charge(50000, nights=2, rooms=1, price_unit="unstated", source_id="flyai")
    assert charge.total_fen is None
    assert charge.basis == "currency_unconfirmed"


def test_a_tax_presumption_only_applies_where_the_table_says_unknown():
    """A presumption on a *verified* or *unsupported* field would be a lie."""
    from stay_agent.qualification import (
        FIELD_STATUS,
        PRESUMED_TAX_INCLUDED,
        PRICE_INCLUDES_TAX,
    )

    for source in PRESUMED_TAX_INCLUDED:
        assert FIELD_STATUS[source][PRICE_INCLUDES_TAX][0] == "unknown"


def test_a_source_with_a_verified_per_night_unit_is_not_treated_as_presumed():
    charge = p.room_charge(18000, nights=2, rooms=1, price_unit="per_room_night", source_id="rollinggo")
    assert charge.basis == "official_per_room_night"
    assert charge.presumed is False
    assert charge.total_fen == 36000


# --- remaining branches: money units, capacity, cancellation, unknown risk --

def test_a_person_night_fee_scales_with_everyone_staying():
    fees = p.build_fees(
        amount_fen=10000, price_unit="per_room_night", source_id="tuniu",
        tax_included=True, nights=2, rooms=2, adults=2, children=1,
        mandatory_fees=(p.FeeInput("城市税", 100, "person_night"),),
    )
    assert fees.mandatory_fees_fen == 100 * 2 * 6
    assert fees.total_known_fen == 10000 * 2 * 2 + 1200


def test_a_per_room_stay_fee_is_not_multiplied_by_nights():
    fees = p.build_fees(
        amount_fen=10000, price_unit="per_room_night", source_id="tuniu",
        tax_included=True, nights=3, rooms=2, adults=2,
        mandatory_fees=(p.FeeInput("打扫费", 500, "room_stay"),),
    )
    assert fees.mandatory_fees_fen == 1000


def test_a_single_room_per_stay_price_is_not_offset():
    charge = p.room_charge(30000, nights=1, rooms=1, price_unit="per_stay", source_id="tuniu")
    assert charge.basis == "official_per_stay"
    assert charge.presumed is False


def test_an_unstated_unit_is_multiplied_only_as_an_approved_presumption():
    charge = p.room_charge(18000, nights=3, rooms=1, price_unit="unstated", source_id="tuniu")
    assert charge.basis == "user_approved_average_times_nights"
    assert charge.presumed is True
    assert charge.total_fen == 54000
    assert "偏高" in charge.note


def test_a_source_outside_the_domestic_default_keeps_the_tax_treatment_unknown():
    fees = p.build_fees(
        amount_fen=10000, price_unit="per_room_night", source_id="some_new_source",
        tax_included=None, nights=1, rooms=1, adults=2,
    )
    assert fees.tax_basis == "tax_treatment_unknown"
    assert fees.complete is False


def test_a_fee_the_source_already_included_is_not_counted_twice():
    fees = p.build_fees(
        amount_fen=10000, price_unit="per_room_night", source_id="tuniu",
        tax_included=True, nights=1, rooms=1, adults=2,
        mandatory_fees=(p.FeeInput("度假村费", 800, "room_night", included=True),),
    )
    assert fees.mandatory_fees_fen == 0
    assert any("已含在房费里" in line.note for line in fees.lines)


def test_a_capacity_below_the_guests_fails_and_is_never_rounded_up():
    assessment = run(max_occupancy=1, adults=2)
    assert verdicts(assessment)["occupancy_capacity"] == "fail"
    assert assessment.tier == "excluded"


def test_more_guests_than_the_domestic_default_without_a_source_capacity_is_unknown():
    assessment = run(max_occupancy=None, adults=3)
    assert verdicts(assessment)["occupancy_capacity"] == "unknown"
    assert assessment.tier == "reference"


def test_an_invalid_head_count_is_a_programming_error():
    with pytest.raises(ValueError):
        p.occupancy_capacity_judgement(2, adults=0, children=0)
    with pytest.raises(ValueError):
        p.occupancy_capacity_judgement(2, adults=2, children=-1)


def test_no_score_threshold_means_the_score_is_not_a_gate():
    row = p.guest_score_judgement(4.1, "5", None)
    assert row.verdict == "pass"
    assert row.basis == "threshold_not_required"


def test_a_structured_free_cancel_flag_passes_without_parsing_the_text():
    row = p.cancellation_judgement("见条款", verified=True, require_free_cancel=True)
    assert row.verdict == "pass"
    assert row.presumed is False


def test_free_cancel_text_without_a_marker_is_unknown_not_a_pass():
    row = p.cancellation_judgement("入住前可协商", verified=None, require_free_cancel=True)
    assert row.verdict == "unknown"
    assert row.presumed is False


def test_a_negative_budget_is_rejected():
    with pytest.raises(ValueError):
        p.check_budget(
            p.build_fees(amount_fen=1, price_unit="per_room_night", source_id="tuniu",
                         tax_included=True, nights=1, rooms=1, adults=2),
            (), -1, nights=1, rooms=1,
        )


def test_an_averaged_pass_with_unknown_costs_states_both_risks():
    assessment = run(
        nights=2, amount_fen=18000, budget_fen=20000,
        mandatory_fees=(p.FeeInput("度假村费", 5000, "room_night", verified=False),),
    )
    assert assessment.budget.basis == "average_presumed"
    assert "无法排除其中一晚超预算" in assessment.budget.detail
    assert "未核实费用未计入" in assessment.budget.detail


def test_the_presumed_helper_exposes_which_rows_rest_on_a_default():
    assessment = run(room_name="独卫大床房")
    labels = {row.label for row in assessment.presumed}
    assert "整间客房" in labels
    assert assessment.qualified is (assessment.tier == "qualified")


def test_an_unknown_period_or_negative_headcount_is_refused():
    with pytest.raises(ValueError):
        p.fee_total_fen(100, "nightly", nights=1, rooms=1, persons=2)
    with pytest.raises(ValueError):
        p.fee_total_fen(100, "person_night", nights=1, rooms=1, persons=-1)


def test_a_source_capacity_at_the_limit_still_passes():
    assessment = run(max_occupancy=2, adults=2, children=0)
    row = next(r for r in assessment.conditions if r.condition == "occupancy_capacity")
    assert row.verdict == "pass"
    assert row.presumed is False
