"""Untrusted model proposals only; no hotel facts, executable tools or state writes."""

import json
from difflib import SequenceMatcher
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
import re


class ProposalError(Exception):
    def __init__(self, code="INVALID_MODEL_OUTPUT"):
        self.code = code
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Evidence = Annotated[str, Field(min_length=1, max_length=300)]
TextValue = Annotated[str, Field(min_length=1, max_length=120)]


class TextChange(StrictModel):
    field: Literal["place", "check_in", "check_out"]
    value: TextValue | None
    evidence: Evidence


class TypeChange(StrictModel):
    field: Literal["place_type"]
    value: Literal["城市", "景点", "地铁站", "区/县", "详细地址", "酒店", "火车站", "机场"] | None
    evidence: Evidence


class IntChange(StrictModel):
    field: Literal["adults", "rooms", "nights", "children_count", "budget_fen"]
    value: StrictInt | None
    evidence: Evidence

    @model_validator(mode="after")
    def bounds(self):
        limits = {
            "adults": (1, 8),
            "rooms": (1, 4),
            "nights": (1, 28),
            "children_count": (0, 4),
            "budget_fen": (0, 100000000),
        }
        low, high = limits[self.field]
        if self.value is not None and not low <= self.value <= high:
            raise ValueError("outside field bounds")
        return self


class AgesChange(StrictModel):
    field: Literal["children_ages"]
    value: list[Annotated[StrictInt, Field(ge=0, le=17)]] | None = Field(max_length=4)
    evidence: Evidence


class BasisChange(StrictModel):
    field: Literal["budget_basis"]
    value: Literal["room_night", "stay_total"] | None
    evidence: Evidence


Change = Annotated[
    Union[TextChange, TypeChange, IntChange, AgesChange, BasisChange], Field(discriminator="field")
]


class NoteChange(StrictModel):
    text: TextValue
    remove: StrictBool
    evidence: Evidence


class VisitChange(StrictModel):
    """A user-described visit, never an independently verified business/POI."""
    name: TextValue
    category: Literal["餐饮", "景点", "交通", "其他"]
    area_hint: TextValue | None = None
    stay_near: StrictBool = False
    action: Literal["add", "update", "remove"] = "add"
    evidence: Evidence


class ConditionProposal(StrictModel):
    changes: list[Change] = Field(max_length=16)
    notes: list[NoteChange] = Field(max_length=8)
    visits: list[VisitChange] = Field(default_factory=list, max_length=6)


def parse_proposal(raw, user_inputs, existing_notes=(), existing_visits=()):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 32768:
        raise ProposalError()

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError("non-JSON constant")

    try:
        data = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
        parsed = ConditionProposal.model_validate(data)
    except (ValueError, TypeError, RecursionError):
        raise ProposalError() from None
    # A model may opportunistically clear an unrelated field while editing a
    # restaurant branch. A null value must have user wording about cancelling
    # or postponing THAT category; never erase a committed stay silently.
    clear_terms = re.compile(r"取消|清除|删掉|删除|不要|不设|不确定|未确定|未定|没定|待定|不限|暂时不|改天|重新安排|去掉")
    field_terms = {
        "place": r"地点|目的地|城市|地区|住哪",
        "place_type": r"地点|目的地|城市|地址|类型",
        "check_in": r"入住|日期|哪天",
        "check_out": r"退房|离店|日期|晚数",
        "nights": r"住几晚|晚数|日期|退房",
        "rooms": r"房间|房数|几间",
        "adults": r"成人|大人|人数",
        "children_count": r"儿童|小孩|孩子",
        "children_ages": r"儿童|小孩|孩子|年龄",
        "budget_fen": r"预算|价格|花费",
        "budget_basis": r"预算|每房|每晚|总价",
    }
    changed = set()
    for change in parsed.changes:
        if change.field in changed:
            raise ProposalError()
        changed.add(change.field)
        if not any(change.evidence in message for message in user_inputs):
            raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
        if change.value is None and not (
            clear_terms.search(change.evidence)
            and re.search(field_terms[change.field], change.evidence)
        ):
            raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
    def drops_proximity(evidence):
        return bool(re.search(
            r"(?:不用|不要|不想|不必|不再|无需|无须|取消|别|没必要)"
            r".{0,20}(?:附近|靠近|周边|旁边|周围|就近)",
            evidence,
        ))

    known_visits = {item["name"] for item in existing_visits}
    seen_visits = set()
    for visit in parsed.visits:
        if visit.name in seen_visits:
            raise ProposalError("DUPLICATE_VISIT")
        seen_visits.add(visit.name)
        if not any(visit.evidence in message for message in user_inputs):
            raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
        if visit.area_hint is not None and visit.area_hint not in visit.evidence:
            raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
        if visit.action == "add" and visit.name not in visit.evidence:
            raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
        if visit.action in ("update", "remove") and visit.name not in known_visits:
            raise ProposalError("UNKNOWN_VISIT")
        if visit.action == "update" and visit.name not in visit.evidence:
            same_category = [
                item for item in existing_visits
                if item["category"] == visit.category
            ]
            if (
                (not visit.area_hint and not visit.stay_near)
                or len(same_category) != 1
                or same_category[0]["name"] != visit.name
            ):
                raise ProposalError("AMBIGUOUS_VISIT_REFERENCE")
        if visit.action == "remove" and visit.name not in visit.evidence:
            raise ProposalError("AMBIGUOUS_VISIT_REFERENCE")
        if visit.action == "update" and not visit.stay_near:
            prior = next(
                (item for item in existing_visits if item["name"] == visit.name),
                None,
            )
            if prior and prior.get("stay_near") and not drops_proximity(visit.evidence):
                # Absent a clear refusal, False is usually the model's default
                # and cannot silently undo a previously confirmed preference.
                visit.stay_near = True
        if visit.stay_near and not re.search(
            r"附近|靠近|周边|周围|旁边|就近|步行|走路|离.{0,15}近",
            visit.evidence,
        ):
            # Updating a branch does not create a NEW proximity claim when
            # this same visit was already marked "stay near" in the draft.
            prior = next(
                (item for item in existing_visits if item["name"] == visit.name),
                None,
            )
            if visit.action != "update" or not prior or not prior.get("stay_near"):
                raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
    kept_notes = []
    for note in parsed.notes:
        if not any(note.evidence in message for message in user_inputs):
            raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
        if note.remove:
            if note.text not in existing_notes:
                # Sometimes a model duplicates a visit-proximity cancellation
                # as a note removal although no such note exists. Ignore ONLY
                # that redundant no-op, while applying the evidenced visit edit.
                redundant = any(
                    visit.action == "update"
                    and not visit.stay_near
                    and visit.name in note.evidence
                    and drops_proximity(note.evidence)
                    and any(
                        old["name"] == visit.name and old.get("stay_near")
                        for old in existing_visits
                    )
                    for visit in parsed.visits
                )
                if not redundant:
                    raise ProposalError("UNKNOWN_CONDITION_NOTE")
                continue
        elif not any(note.text in message for message in user_inputs):
            # If the model lightly paraphrases a note, replace it with the
            # *verbatim* evidence substring, never keep the new claim.
            # Materially different or overlong additions are still rejected.
            if (
                len(note.evidence) > 120
                or SequenceMatcher(None, note.text, note.evidence).ratio() < 0.72
            ):
                raise ProposalError("UNSUPPORTED_MODEL_EVIDENCE")
            note.text = note.evidence
        kept_notes.append(note)
    parsed.notes = kept_notes
    return parsed
