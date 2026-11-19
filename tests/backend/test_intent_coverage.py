"""Deterministic high-confidence omission checks; synthetic language only."""
import pytest

from stay_agent.intent_coverage import require_explicit_intent_coverage
from stay_agent.model_output import ProposalError


def check(text, **draft):
    require_explicit_intent_coverage(text, draft)


@pytest.mark.parametrize(
    "text,draft",
    [
        ("改去深圳，要两间房", {"place": "广州", "place_type": "城市", "rooms": 2}),
        ("改成深圳，2间房", {"place": "深圳", "place_type": "城市", "rooms": 1}),
        ("改成深圳，2间房", {"place": "深圳", "place_type": None, "rooms": 2}),
        ("每房每晚300元", {"budget_fen": 20000, "budget_basis": "room_night"}),
        ("每房每晚300元", {"budget_fen": 30000, "budget_basis": "stay_total"}),
        ("不要预算限制", {"budget_fen": 30000}),
    ],
)
def test_detect_explicit_omissions(text, draft):
    with pytest.raises(ProposalError, match="SEMANTIC_OMISSION"):
        require_explicit_intent_coverage(text, draft)


@pytest.mark.parametrize(
    "text,draft",
    [
        ("改去深圳，要两间房", {"place": "深圳", "place_type": "城市", "rooms": 2}),
        ("改成深圳，2间房", {"place": "深圳", "place_type": "城市", "rooms": 2}),
        ("改成深圳，不对，还是广州", {"place": "广州", "place_type": "城市"}),
        ("不要改去深圳", {"place": "广州", "place_type": "城市"}),
        ("不要去深圳", {"place": "广州", "place_type": "城市"}),
        ("不要两间房，改为一间房", {"rooms": 1}),
        ("不要两间房", {"rooms": 1}),
        ("我不想要两间房", {"rooms": 1}),
        ("两间房还是一间房？", {"rooms": None}),
        ("这家酒店只剩两间房", {"rooms": 1}),
        ("取消预算", {"budget_fen": None, "budget_basis": None}),
        ("每房每晚300元", {"budget_fen": 30000, "budget_basis": "room_night"}),
        ("先取消预算，改为每房每晚300元", {"budget_fen": 30000, "budget_basis": "room_night"}),
        ("不限预算", {"budget_fen": None}),
        ("我想去深圳南山区", {"place": "广州", "place_type": "城市"}),
        ("请找两间房?", {"rooms": None}),
    ],
)
def test_no_false_positive_for_matched_or_ambiguous_intent(text, draft):
    require_explicit_intent_coverage(text, draft)


def test_checks_latest_message_not_old_earlier_pending_inputs():
    # The caller passes user_inputs[-1]; an earlier abandoned city cannot demand a new change.
    check("改去杭州", place="杭州", place_type="城市")
