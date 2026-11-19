"""Colloquial evidence and explicit mainland scope, with synthetic proposals."""
from datetime import date
import json
import pytest
from stay_agent.condition_rules import empty_draft, merge_conditions, describe_conditions
from stay_agent.model_output import parse_proposal, ProposalError

TODAY = date(2026, 11, 16)
TEXT = "我想去广州，2026-11-23住一宿，就一间，两位大人，没小孩儿，一晚上别超过三百块。"

def merge(text=TEXT, **overrides):
    fields = {"place": "广州", "place_type": "城市", "check_in": "2026-11-23",
              "nights": 1, "rooms": 1, "adults": 2, "children_count": 0,
              "budget_fen": 30000, "budget_basis": "room_night", **overrides}
    raw = json.dumps({"changes": [{"field": k, "value": v, "evidence": text}
                                  for k, v in fields.items()], "notes": [], "visits": []})
    return merge_conditions(empty_draft(), parse_proposal(raw, [text]), TODAY, latest_input=text)

def test_complete_colloquial_input_with_valid_model_proposal():
    draft = merge()
    query, missing, _, _ = describe_conditions(draft, TODAY)
    assert query and not missing
    assert query.budget_fen == 30000 and query.children_ages == []

@pytest.mark.parametrize("phrase", ["没小孩儿", "没孩子", "不带小孩儿", "没有小朋友"])
def test_explicit_colloquial_no_children(phrase):
    draft = merge(TEXT.replace("没小孩儿", phrase))
    assert draft["children_count"] == 0

def test_chinese_budget_evidence_cannot_support_different_amount():
    with pytest.raises(ProposalError, match="BUDGET_EVIDENCE_MISMATCH"):
        merge(budget_fen=50000)

@pytest.mark.parametrize("place", ["东京新宿", "香港尖沙咀", "澳门", "台北", "Tokyo Shinjuku"])
def test_known_outside_mainland_place_never_produces_ready_query(place):
    draft = merge(TEXT.replace("广州", place), place=place)
    query, missing, question, warnings = describe_conditions(draft, TODAY)
    assert query is None and "中国大陆目的地" in missing
    assert "中国大陆" in question and any("中国大陆" in w for w in warnings)

def test_mainland_name_containing_foreign_hotel_name_is_not_blocked():
    draft = merge(TEXT.replace("广州", "上海东京酒店"), place="上海东京酒店", place_type="酒店")
    assert describe_conditions(draft, TODAY)[0] is not None
