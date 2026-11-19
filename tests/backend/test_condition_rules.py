"""Condition arithmetic and unknown-state boundaries, independent of any model."""

from datetime import date
import pytest
from stay_agent.condition_rules import empty_draft, merge_conditions, describe_conditions
from stay_agent.model_output import parse_proposal, ProposalError
from tests.backend.test_conversation import initial, raw, change, message

TODAY = date(2026, 10, 17)


def ready():
    return merge_conditions(empty_draft(), parse_proposal(initial(), [message()]), TODAY)


def apply(base, changes, text):
    return merge_conditions(base, parse_proposal(raw(changes), [text]), TODAY)


def test_stay_end_is_derived_from_explicit_start_and_nights():
    state = ready()
    assert state["check_out"] == "2026-11-08"
    assert describe_conditions(state, TODAY)[0].children_ages == []


def test_changed_arrival_recomputes_end_with_known_stay_duration():
    state = apply(ready(), [change("check_in", "2026-11-09", "2026年11月9日")], "改为2026年11月9日")
    assert state["check_out"] == "2026-11-10" and state["adults"] == 2


def test_explicit_unknown_checkout_does_not_reuse_an_old_derived_end():
    state = apply(ready(), [change("check_out", None, "退房未定")], "退房未定")
    assert state["check_out"] is None and state["nights"] is None
    assert describe_conditions(state, TODAY)[0] is None


def test_clearing_budget_also_clears_old_basis():
    state = apply(ready(), [change("budget_fen", None, "不限预算")], "不限预算")
    assert state["budget_basis"] is None and describe_conditions(state, TODAY)[0].budget_fen is None


def test_total_stay_budget_is_not_silently_used_as_a_nightly_budget():
    state = apply(ready(), [change("budget_basis", "stay_total", "总预算200元")], "总预算200元")
    query, missing, _, warnings = describe_conditions(state, TODAY)
    assert query is None and "每房每晚预算口径" in missing and warnings


def test_incomplete_children_ages_never_reach_price_query():
    state = apply(
        ready(),
        [change("children_count", 2, "2个儿童"), change("children_ages", [5], "5岁")],
        "2个儿童，有个5岁",
    )
    assert describe_conditions(state, TODAY)[0] is None
    assert state["adults"] == 2 and state["children_ages"] == [5]


def test_relative_date_cannot_disagree_with_china_today():
    with pytest.raises(ProposalError, match="DATE_EVIDENCE_MISMATCH"):
        apply(ready(), [change("check_in", "2026-10-19", "明天")], "明天")


def test_amount_must_match_quoted_yuan_in_integer_fen():
    with pytest.raises(ProposalError, match="BUDGET_EVIDENCE_MISMATCH"):
        apply(ready(), [change("budget_fen", 150000, "150元")], "每晚150元")


def test_no_children_cannot_be_inferred_from_unspecified_people():
    with pytest.raises(ProposalError, match="CHILD_COUNT_EVIDENCE_MISMATCH"):
        apply(empty_draft(), [change("children_count", 0, "两个人")], "两个人")


def test_past_dates_stay_a_clarification_not_a_valid_quote():
    state = apply(ready(), [change("check_in", "2026-10-01", "2026年10月1日")], "2026年10月1日")
    query, missing, _, _ = describe_conditions(state, TODAY)
    assert query is None and "有效入住日期" in missing


def test_conflicting_explicit_duration_requires_clarification():
    state = apply(
        ready(),
        [change("check_out", "2026-11-09", "2026年11月9日"), change("nights", 1, "住1晚")],
        "2026年11月9日退房但住1晚",
    )
    assert describe_conditions(state, TODAY)[0] is None


def test_extra_conditions_remain_separate_from_query_eligibility():
    state = ready()
    state["notes"] = ["独立卫浴", "海底捞附近"]
    query, _, _, warnings = describe_conditions(state, TODAY)
    assert query and warnings
    assert state["notes"] == ["独立卫浴", "海底捞附近"]
