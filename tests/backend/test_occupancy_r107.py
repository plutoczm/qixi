"""Per-room semantics must survive erroneous model proposals and later edits."""
from datetime import date
import json
import pytest
from stay_agent.condition_rules import empty_draft, merge_conditions, describe_conditions
from stay_agent.model_output import parse_proposal

TODAY = date(2026, 11, 16)

def apply(text, values, base=None):
    raw = json.dumps({"changes": [{"field": k, "value": v, "evidence": text}
                                  for k, v in values.items()], "notes": [], "visits": []})
    return merge_conditions(base or empty_draft(), parse_proposal(raw, [text]), TODAY,
                            latest_input=text)

def ready():
    return {**empty_draft(), "place": "广州", "place_type": "城市",
            "check_in": "2026-11-23", "check_out": "2026-11-24", "nights": 1,
            "rooms": 1, "adults": 2, "children_count": 0, "children_ages": []}

@pytest.mark.parametrize("text", ["2间房，一共3个大人", "两间房，3位成人", "2间房，总共4成人"])
def test_total_or_unspecified_multiroom_people_require_distribution(text):
    state = apply(text, {"rooms": 2, "adults": 3}, ready())
    assert state["adults"] is None
    query, missing, question, warnings = describe_conditions(state, TODAY)
    assert query is None and "每房成人数" in missing
    assert "分别查询" in question or any("分别查询" in w for w in warnings)

def test_room_change_cannot_reuse_single_room_people():
    state = apply("改成2间房", {"rooms": 2}, ready())
    assert state["adults"] is None

def test_total_people_edit_cannot_reuse_old_per_room_value_when_model_omits_it():
    state = apply("一共3个大人", {}, {**ready(), "rooms": 2})
    assert state["adults"] is None

def test_unequal_distribution_requires_separate_queries():
    state = apply("2间房，一间2成人，另一间1成人", {"rooms": 2, "adults": 2}, ready())
    assert describe_conditions(state, TODAY)[0] is None

@pytest.mark.parametrize("text", ["2间房，每房2成人，无儿童", "2间房，每间住2位大人，无儿童"])
def test_explicit_uniform_per_room_people_can_be_queried(text):
    state = apply(text, {"rooms": 2, "adults": 2, "children_count": 0}, ready())
    assert describe_conditions(state, TODAY)[0].adults == 2

def test_single_room_total_is_valid():
    state = apply("1间房，一共3个大人", {"rooms": 1, "adults": 3}, ready())
    assert describe_conditions(state, TODAY)[0].adults == 3

def test_multiroom_children_total_and_ages_are_not_per_room():
    state = apply("2间房，每房2成人，总共1个儿童，6岁",
                  {"rooms": 2, "adults": 2, "children_count": 1, "children_ages": [6]}, ready())
    assert state["adults"] == 2
    assert state["children_count"] is None and state["children_ages"] is None
    assert describe_conditions(state, TODAY)[0] is None

def test_unrelated_edit_preserves_confirmed_occupancy():
    state = apply("住2晚", {"nights": 2}, {**ready(), "rooms": 2})
    assert describe_conditions(state, TODAY)[0].adults == 2

def test_explicit_followup_resolves_ambiguity():
    state = apply("2间房，一共3个大人", {"rooms": 2, "adults": 3}, ready())
    state = apply("改成每房2成人", {"adults": 2}, state)
    assert describe_conditions(state, TODAY)[0].adults == 2


def test_explicit_per_room_evidence_must_match_proposed_count():
    state = apply("2间房，每房2成人", {"rooms": 2, "adults": 3}, ready())
    assert state["adults"] is None

def test_generic_people_total_invalidates_existing_multiroom_adults():
    state = apply("一共3个人", {}, {**ready(), "rooms": 2})
    assert describe_conditions(state, TODAY)[0] is None


def test_age_only_followup_preserves_confirmed_per_room_child_count():
    base = {**ready(), "rooms": 2, "children_count": 1, "children_ages": None}
    state = apply("6岁", {"children_ages": [6]}, base)
    assert state["children_count"] == 1 and state["children_ages"] == [6]
    assert describe_conditions(state, TODAY)[0] is not None


def test_pending_inputs_preserve_per_room_evidence_before_unrelated_latest_message():
    text = "2间房，每房2成人，无儿童"
    proposal = parse_proposal(json.dumps({"changes": [
        {"field": "rooms", "value": 2, "evidence": text},
        {"field": "adults", "value": 2, "evidence": text}], "notes": []}), [text, "广州"])
    state = merge_conditions(ready(), proposal, TODAY, user_inputs=[text, "广州"])
    assert state["adults"] == 2


def test_latest_total_overrides_earlier_explicit_per_room_in_pending_batch():
    first, last = "2间房，每房2成人，无儿童", "其实一共3个大人"
    proposal = parse_proposal(json.dumps({"changes": [
        {"field": "rooms", "value": 2, "evidence": first},
        {"field": "adults", "value": 3, "evidence": last}], "notes": []}), [first,last])
    state = merge_conditions(ready(), proposal, TODAY, user_inputs=[first,last])
    assert state["adults"] is None


def test_room_specific_exception_blocks_uniform_query_even_without_model_changes():
    state = apply("每房2成人，但其中一间只有1成人", {}, {**ready(), "rooms": 2})
    assert describe_conditions(state, TODAY)[0] is None
