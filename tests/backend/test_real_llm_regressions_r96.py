"""R96 regressions from real DeepSeek outputs; no model network in these tests."""
import json

import pytest
from stay_agent.model_output import parse_proposal, ProposalError
from stay_agent.condition_rules import empty_draft, merge_conditions


def wire(changes=(), notes=(), visits=()):
    return json.dumps({"changes": list(changes), "notes": list(notes),
                       "visits": list(visits)}, ensure_ascii=False)


def test_real_brand_only_rewrites_small_model_paraphrase_to_literal_evidence():
    message = "想去海底捞吃火锅，晚上希望住它附近，最好出地铁站就能走到酒店。"
    parsed = parse_proposal(wire(notes=[
        {"text": "希望出地铁站就能走到酒店", "remove": False,
         "evidence": "最好出地铁站就能走到酒店"}
    ], visits=[
        {"name": "海底捞", "category": "餐饮", "area_hint": None,
         "stay_near": True, "action": "add",
         "evidence": "想去海底捞吃火锅，晚上希望住它附近"}
    ]), [message])
    result = merge_conditions(empty_draft(), parsed)
    assert result["notes"] == ["最好出地铁站就能走到酒店"]
    assert result["visits"][0]["stay_near"] is True
    assert result["place"] is None  # brand is not an invented city


def test_real_second_model_attempt_does_not_lose_visitor_context():
    message = "想去海底捞吃火锅，晚上希望住它附近，最好出地铁站就能走到酒店。"
    parsed = parse_proposal(wire(notes=[
        {"text": "晚上希望住海底捞附近", "remove": False,
         "evidence": "晚上希望住它附近"},
        {"text": "最好出地铁站就能走到酒店", "remove": False,
         "evidence": "最好出地铁站就能走到酒店"},
    ], visits=[
        {"name": "海底捞", "category": "餐饮", "area_hint": None,
         "stay_near": True, "action": "add",
         "evidence": "想去海底捞吃火锅，晚上希望住它附近"},
    ]), [message])
    assert parsed.notes[0].text == "晚上希望住它附近"


def test_real_branch_followup_preserves_prior_proximity_without_reassertion():
    old = {**empty_draft(), "visits": [
        {"name": "海底捞", "category": "餐饮", "area_hint": None,
         "stay_near": True},
        {"name": "广州塔", "category": "景点", "area_hint": None,
         "stay_near": False},
    ]}
    message = "海底捞是荔胜广场那家。"
    proposal = parse_proposal(wire(visits=[
        {"name": "海底捞", "category": "餐饮", "area_hint": "荔胜广场",
         "stay_near": True, "action": "update", "evidence": message},
    ]), [message], old["notes"], old["visits"])
    result = merge_conditions(old, proposal)
    assert result["visits"][0]["area_hint"] == "荔胜广场"
    assert result["visits"][0]["stay_near"] is True
    assert result["visits"][1]["name"] == "广州塔"


def test_new_proximity_claim_without_user_evidence_stays_rejected():
    old = {**empty_draft(), "visits": [
        {"name": "海底捞", "category": "餐饮", "area_hint": None,
         "stay_near": False}
    ]}
    message = "海底捞是荔胜广场那家。"
    with pytest.raises(ProposalError, match="UNSUPPORTED_MODEL_EVIDENCE"):
        parse_proposal(wire(visits=[
            {"name": "海底捞", "category": "餐饮", "area_hint": "荔胜广场",
             "stay_near": True, "action": "update", "evidence": message},
        ]), [message], old["notes"], old["visits"])


@pytest.mark.parametrize("field", ["check_in", "check_out", "place", "rooms"])
def test_unrelated_branch_update_must_not_erase_existing_stay_conditions(field):
    message = "海底捞是荔胜广场那家。"
    with pytest.raises(ProposalError, match="UNSUPPORTED_MODEL_EVIDENCE"):
        parse_proposal(wire(changes=[
            {"field": field, "value": None, "evidence": message}
        ]), [message])


def test_explicit_clear_intent_still_works_for_matching_field():
    message = "不确定退房日期，先清除退房日期"
    parsed = parse_proposal(wire(changes=[
        {"field": "check_out", "value": None, "evidence": message}
    ]), [message])
    assert parsed.changes[0].value is None


@pytest.mark.parametrize("claim,evidence", [
    ("保证独立卫浴", "我希望有独立卫浴"),
    ("保证已经预订成功", "预订还没确定"),
    ("保证免费取消", "取消政策不确定"),
])
def test_model_cannot_turn_wishes_into_guarantees(claim, evidence):
    with pytest.raises(ProposalError, match="UNSUPPORTED_MODEL_EVIDENCE"):
        parse_proposal(wire(notes=[
            {"text": claim, "remove": False, "evidence": evidence}
        ]), [evidence])


def test_cannot_normalize_overlong_evidence_as_short_note():
    msg = "入住要求" * 40
    with pytest.raises(ProposalError, match="UNSUPPORTED_MODEL_EVIDENCE"):
        parse_proposal(wire(notes=[
            {"text": "入住需 求" * 16, "remove": False, "evidence": msg}
        ]), [msg])


def test_real_negated_restaurant_proximity_does_not_delete_the_restaurant():
    message = "我还是要去海底捞吃火锅，但酒店不用住在海底捞附近了。"
    base = {**empty_draft(), "visits": [
        {"name": "海底捞", "category": "餐饮", "area_hint": None, "stay_near": True}
    ]}
    parsed = parse_proposal(wire(
        notes=[{"text": "酒店不用住在海底捞附近", "remove": True,
                "evidence": "但酒店不用住在海底捞附近了"}],
        visits=[{"name": "海底捞", "category": "餐饮", "area_hint": None,
                 "stay_near": False, "action": "update", "evidence": message}],
    ), [message], base["notes"], base["visits"])
    merged = merge_conditions(base, parsed)
    assert parsed.notes == []  # redundant removal of a nonexistent note
    assert len(merged["visits"]) == 1
    assert merged["visits"][0]["stay_near"] is False


def test_omitted_proximity_flag_is_not_a_cancellation():
    base = {**empty_draft(), "visits": [
        {"name": "海底捞", "category": "餐饮", "area_hint": None, "stay_near": True}
    ]}
    message = "海底捞改去荔胜广场店"
    parsed = parse_proposal(wire(visits=[
        {"name": "海底捞", "category": "餐饮", "area_hint": "荔胜广场",
         "stay_near": False, "action": "update", "evidence": message}
    ]), [message], base["notes"], base["visits"])
    assert merge_conditions(base, parsed)["visits"][0]["stay_near"] is True


def test_unknown_note_removal_still_requires_relevant_explicit_visit_reversal():
    message = "不要给我保证都能预订"
    with pytest.raises(ProposalError, match="UNKNOWN_CONDITION_NOTE"):
        parse_proposal(wire(notes=[
            {"text": "保证都能预订", "remove": True, "evidence": message}
        ]), [message])


@pytest.mark.parametrize("field,evidence", [
    ("check_out", "退房未定"),
    ("budget_fen", "不限预算"),
])
def test_existing_explicit_unknown_or_unlimited_wording_remains_valid(field, evidence):
    parsed = parse_proposal(wire(changes=[
        {"field": field, "value": None, "evidence": evidence}
    ]), [evidence])
    assert parsed.changes[0].value is None
