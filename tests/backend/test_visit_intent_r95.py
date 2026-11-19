"""R95: LLM may recognize planned places, but cannot invent POI identity."""
from datetime import date
import json

import pytest

from stay_agent.condition_rules import describe_conditions, empty_draft, merge_conditions
from stay_agent.intent_coverage import require_explicit_intent_coverage
from stay_agent.model_output import ProposalError, parse_proposal


def proposal(visits, text, base=None, changes=None):
    base = base or empty_draft()
    raw = json.dumps(
        {"changes": changes or [], "notes": [], "visits": visits}, ensure_ascii=False
    )
    return parse_proposal(raw, [text], base["notes"], base["visits"])


def visit(name, evidence, *, category="餐饮", area_hint=None,
          stay_near=False, action="add"):
    return {
        "name": name, "category": category, "area_hint": area_hint,
        "stay_near": stay_near, "action": action, "evidence": evidence,
    }


def test_restaurant_visit_is_separate_from_hotel_city_and_not_a_verified_poi():
    text = "我想去海底捞吃火锅，住在海底捞附近"
    draft = merge_conditions(empty_draft(), proposal([
        visit("海底捞", text, stay_near=True),
    ], text))
    assert draft["place"] is None
    assert draft["visits"] == [
        {"name": "海底捞", "category": "餐饮", "area_hint": None, "stay_near": True}
    ]
    require_explicit_intent_coverage(text, draft)
    query, missing, _, warnings = describe_conditions(draft, date(2026, 11, 12))
    assert query is None and "目的地" in missing
    assert any("尚未核实" in row for row in warnings)


def test_multiple_visit_categories_survive_new_turns_without_automatic_routing():
    text = "广州要去海底捞吃饭，也要去广州塔玩"
    visits = [
        visit("海底捞", text),
        visit("广州塔", text, category="景点"),
    ]
    draft = merge_conditions(empty_draft(), proposal(visits, text))
    assert len(draft["visits"]) == 2
    update_text = "海底捞想住附近"
    next_draft = merge_conditions(draft, proposal([
        visit("海底捞", update_text, stay_near=True, action="update"),
    ], update_text, draft))
    assert next_draft["visits"][0]["stay_near"] is True
    assert next_draft["visits"][1]["name"] == "广州塔"
    assert next_draft["place"] is None


def test_one_place_can_be_qualified_by_a_later_branch_reply():
    initial = "我想去海底捞吃火锅"
    original = merge_conditions(
        empty_draft(), proposal([visit("海底捞", initial)], initial)
    )
    clarification = "是荔胜广场那家"
    changed = proposal([
        visit("海底捞", clarification, area_hint="荔胜广场", action="update"),
    ], clarification, original)
    result = merge_conditions(original, changed)
    assert result["visits"] == [
        {"name": "海底捞", "category": "餐饮", "area_hint": "荔胜广场",
         "stay_near": False}
    ]


def test_explicit_removal_only_and_multiple_visits_not_ambiguous():
    message = "还想去广州塔"
    original = merge_conditions(
        empty_draft(), proposal([visit("广州塔", message, category="景点")], message)
    )
    removed = merge_conditions(original, proposal([
        visit("广州塔", "不去广州塔了", category="景点", action="remove"),
    ], "不去广州塔了", original))
    assert removed["visits"] == []
    with pytest.raises(ProposalError, match="UNKNOWN_VISIT"):
        proposal([visit("海底捞", "不去海底捞了", action="remove")], "不去海底捞了")


@pytest.mark.parametrize("visits,text", [
    ([visit("海底捞火锅(荔胜广场店)", "想去海底捞")], "想去海底捞"),
    ([visit("海底捞", "想去海底捞", area_hint="荔胜广场")], "想去海底捞"),
    ([visit("海底捞", "我想去海底捞", stay_near=True)], "我想去海底捞"),
    ([visit("海底捞", "去海底捞"), visit("海底捞", "去海底捞")], "去海底捞"),
])
def test_visit_hallucinations_are_rejected(visits, text):
    with pytest.raises(ProposalError):
        proposal(visits, text)


def test_explicit_haidilao_intent_omission_requires_repair_not_silent_drop():
    with pytest.raises(ProposalError, match="SEMANTIC_OMISSION"):
        require_explicit_intent_coverage("去海底捞吃火锅", empty_draft())
    # A user cancellation must not be treated as a newly missing positive intent.
    require_explicit_intent_coverage("不去海底捞了", empty_draft())


def test_legacy_model_outputs_without_visits_remain_parseable():
    result = parse_proposal('{"changes":[],"notes":[]}', ["就住这里"])
    assert result.visits == []


def test_unique_restaurant_can_be_clarified_even_with_other_sights():
    text = "还想去海底捞吃火锅和广州塔"
    base = merge_conditions(empty_draft(), proposal([
        visit("海底捞", text),
        visit("广州塔", text, category="景点"),
    ], text))
    reply = "是荔胜广场那一家"
    result = merge_conditions(base, proposal([
        visit("海底捞", reply, action="update", area_hint="荔胜广场"),
    ], reply, base))
    assert result["visits"][0]["area_hint"] == "荔胜广场"
    assert result["visits"][1]["area_hint"] is None


def test_two_restaurants_make_implicit_branch_reply_ambiguous():
    text = "去海底捞和小龙坎吃饭"
    base = merge_conditions(empty_draft(), proposal([
        visit("海底捞", text),
        visit("小龙坎", text),
    ], text))
    reply = "是荔胜广场那一家"
    with pytest.raises(ProposalError, match="AMBIGUOUS_VISIT_REFERENCE"):
        proposal([visit("海底捞", reply, action="update", area_hint="荔胜广场")],
                 reply, base)


@pytest.mark.parametrize("negative", [
    "不去海底捞了",
    "不要去海底捞",
    "我不想住在海底捞附近",
    "不需要海底捞周边的酒店",
])
def test_restaurant_omission_guard_does_not_require_negated_intent(negative):
    require_explicit_intent_coverage(negative, empty_draft())
