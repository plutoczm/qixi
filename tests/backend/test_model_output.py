"""Schema/evidence guard regressions. Does not claim live semantic model quality."""

import json
import pytest
from stay_agent.model_output import parse_proposal, ProposalError, ConditionProposal


def wire(changes, notes=None):
    return json.dumps({"changes": changes, "notes": notes or []}, ensure_ascii=False)


def test_only_evidenced_typed_fields_can_be_proposed():
    result = parse_proposal(
        wire([{"field": "budget_fen", "value": 20000, "evidence": "每晚200元"}]),
        ["广州，每晚200元"],
    )
    assert result.changes[0].value == 20000


@pytest.mark.parametrize(
    "raw",
    [
        '{"changes":[],"notes":[],"price":188}',
        '{"changes":[],"changes":[],"notes":[]}',
        '{"changes":[{"field":"budget_fen","value":NaN,"evidence":"200"}],"notes":[]}',
        'prefix {"changes":[],"notes":[]}',
        '```json\n{"changes":[],"notes":[]}\n```',
        '{"changes":[]}',
    ],
)
def test_untrusted_format_or_unknown_facts_rejected(raw):
    with pytest.raises(ProposalError):
        parse_proposal(raw, ["200"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("adults", True),
        ("rooms", 2.0),
        ("rooms", 5),
        ("adults", 0),
        ("nights", 29),
        ("budget_fen", -1),
        ("children_count", 5),
        ("children_ages", [18]),
        ("children_ages", [True]),
        ("price", 18800),
    ],
)
def test_coercion_invalid_values_and_hotel_facts_are_rejected(field, value):
    with pytest.raises(ProposalError):
        parse_proposal(wire([{"field": field, "value": value, "evidence": "测试"}]), ["测试"])


def test_missing_evidence_or_duplicate_semantic_change_rejected():
    change = {"field": "place", "value": "杭州", "evidence": "杭州"}
    with pytest.raises(ProposalError, match="UNSUPPORTED_MODEL_EVIDENCE"):
        parse_proposal(wire([change]), ["广州"])
    with pytest.raises(ProposalError):
        parse_proposal(wire([change, change]), ["杭州"])


def test_clear_is_explicit_and_not_the_same_as_unchanged():
    empty = parse_proposal(wire([]), ["去广州"])
    clear = parse_proposal(
        wire([{"field": "budget_fen", "value": None, "evidence": "不要预算限制"}]), ["不要预算限制"]
    )
    assert empty.changes == []
    assert clear.changes[0].field == "budget_fen" and clear.changes[0].value is None


def test_unsupported_conditions_remain_quoted_notes_without_fabricated_guarantees():
    raw = wire([], [{"text": "靠近海底捞", "remove": False, "evidence": "靠近海底捞"}])
    assert parse_proposal(raw, ["靠近海底捞"]).notes[0].text == "靠近海底捞"
    with pytest.raises(ProposalError):
        parse_proposal(
            wire([], [{"text": "保证独卫", "remove": False, "evidence": "靠近海底捞"}]),
            ["靠近海底捞"],
        )


def test_cancellation_of_notes_must_reference_existing_condition_and_new_user_text():
    raw = wire([], [{"text": "靠近海底捞", "remove": True, "evidence": "不用靠近海底捞了"}])
    assert parse_proposal(raw, ["不用靠近海底捞了"], ["靠近海底捞"]).notes[0].remove
    with pytest.raises(ProposalError, match="UNKNOWN_CONDITION_NOTE"):
        parse_proposal(raw, ["不用靠近海底捞了"])


def test_schema_root_is_an_object_and_additional_properties_are_forbidden():
    schema = ConditionProposal.model_json_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["required"]) == {"changes", "notes"}
