"""Deterministic condition merging; model prose never becomes a price or guarantee."""

from datetime import date, timedelta
from decimal import Decimal
import re
from pydantic import ValidationError
from .contracts import SearchInput, china_today
from .model_output import ProposalError
from .language_evidence import outside_mainland_hint, chinese_integer

FIELDS = (
    "place",
    "place_type",
    "check_in",
    "check_out",
    "nights",
    "adults",
    "rooms",
    "children_count",
    "children_ages",
    "budget_fen",
    "budget_basis",
)


def empty_draft():
    return {**{key: None for key in FIELDS}, "notes": [], "visits": []}


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _verify_date(change, today):
    if change.value is None:
        return
    parsed = _date(change.value)
    if not parsed:
        raise ProposalError("INVALID_DATE_OUTPUT")
    evidence = change.evidence
    expected = None
    for word, days in (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0)):
        if word in evidence:
            expected = today + timedelta(days=days)
            break
    exact = re.search(r"(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})日?", evidence)
    if exact:
        try:
            expected = date(int(exact[1] or today.year), int(exact[2]), int(exact[3]))
        except ValueError:
            raise ProposalError("INVALID_DATE_OUTPUT") from None
    if expected and parsed != expected:
        raise ProposalError("DATE_EVIDENCE_MISMATCH")


def merge_conditions(base, proposal, today=None, *, latest_input=None, user_inputs=None):
    today = today or china_today()
    draft = {**empty_draft(), **base, "notes": list(base.get("notes", [])),
             "visits": [dict(item) for item in base.get("visits", [])]}
    changed = {change.field: change.value for change in proposal.changes}
    for change in proposal.changes:
        if (
            change.field == "children_count"
            and change.value == 0
            and base.get("children_count") != 0
        ):
            if not re.search(
                r"无儿童|没(?:有)?(?:儿童|小孩儿?|孩子|小朋友)|不带(?:儿童|孩子|小孩儿?|小朋友)|0[个名位]?(?:儿童|孩子|小孩)|只有(?:成人|大人)",
                change.evidence,
            ):
                raise ProposalError("CHILD_COUNT_EVIDENCE_MISMATCH")
        if change.field in ("check_in", "check_out"):
            _verify_date(change, today)
        if change.field == "budget_fen" and change.value is not None:
            amounts = re.findall(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|人民币)", change.evidence)
            chinese_amounts = re.findall(r"([零〇一二两三四五六七八九十百千万]+)\s*(?:元|块|人民币)", change.evidence)
            if chinese_amounts and chinese_integer(chinese_amounts[-1]) * 100 != change.value:
                raise ProposalError("BUDGET_EVIDENCE_MISMATCH")
            if amounts and int(Decimal(amounts[-1]) * 100) != change.value:
                raise ProposalError("BUDGET_EVIDENCE_MISMATCH")
        draft[change.field] = change.value
    for note in proposal.notes:
        if note.remove:
            draft["notes"] = [old for old in draft["notes"] if old != note.text]
        elif note.text not in draft["notes"]:
            draft["notes"].append(note.text)
    if len(draft["notes"]) > 12:
        raise ProposalError("CONDITION_NOTE_LIMIT")
    for visit in proposal.visits:
        existing = next(
            (item for item in draft["visits"] if item["name"] == visit.name), None
        )
        if visit.action == "remove":
            if existing is None:
                raise ProposalError("UNKNOWN_VISIT")
            draft["visits"].remove(existing)
        elif visit.action == "update":
            if existing is None:
                raise ProposalError("UNKNOWN_VISIT")
            if visit.area_hint:
                existing["area_hint"] = visit.area_hint
            # parse_proposal has already verified an explicit negated
            # proximity request, or normalized an unstated default to the old
            # preference. Apply the validated boolean in either direction.
            existing["stay_near"] = visit.stay_near
        elif existing is not None:
            if visit.area_hint:
                existing["area_hint"] = visit.area_hint
            if visit.stay_near:
                existing["stay_near"] = True
        else:
            draft["visits"].append({
                "name": visit.name,
                "category": visit.category,
                "area_hint": visit.area_hint,
                "stay_near": visit.stay_near,
            })
    if len(draft["visits"]) > 5:
        raise ProposalError("VISIT_LIMIT")
    if draft["children_count"] == 0:
        draft["children_ages"] = []
    elif draft["children_count"] is None and draft["children_ages"]:
        draft["children_count"] = len(draft["children_ages"])
    if changed.get("check_in", "unchanged") is None:
        draft["check_out"] = None
    if "check_out" in changed and changed["check_out"] is None:
        draft["nights"] = None
    start = _date(draft["check_in"])
    end = _date(draft["check_out"])
    nights = draft["nights"]
    if (
        start
        and nights
        and ("check_out" not in changed)
        and ("check_in" in changed or "nights" in changed or end is None)
    ):
        draft["check_out"] = (start + timedelta(days=nights)).isoformat()
        end = _date(draft["check_out"])
    if start and end and "check_out" in changed and "nights" not in changed:
        draft["nights"] = (end - start).days
    if "budget_fen" in changed and changed["budget_fen"] is None:
        draft["budget_basis"] = None
    _guard_per_room_occupancy(draft, base, proposal, user_inputs or ([latest_input] if latest_input is not None else None))
    return draft


def _guard_per_room_occupancy(draft, base, proposal, user_inputs):
    """Model evidence alone cannot turn total guests into per-room guests."""
    if not draft.get("rooms") or draft["rooms"] <= 1:
        return
    messages = user_inputs or list(dict.fromkeys(change.evidence for change in proposal.changes))
    room_changed = draft["rooms"] != base.get("rooms")
    changed = {change.field for change in proposal.changes}
    number = r"[0-9一二两三四五六七八九十]+"
    for key, noun in (("adults", r"成人|大人|人(?!民币)"), ("children_count", r"儿童|小孩儿?|孩子")):
        # Pending/superseded messages are one ordered operation; an unrelated
        # last message must not erase earlier explicit occupancy evidence.
        relevant = [message for message in messages if re.search(
            rf"{number}\s*[个名位]?(?:{noun})|{number}\s*(?:间房|间客房|个房间)", message
        )]
        text = relevant[-1] if relevant else ""
        per_room = re.findall(
            rf"(?:每房|每间(?:房)?|每个房间)(?:都|各)?(?:住|入住|有|带)?\s*({number})\s*[个名位]?(?:{noun})",
            text,
        )
        counts = [int(value) if value.isascii() else chinese_integer(value) for value in per_room]
        explicit = bool(counts) and all(value == draft.get(key) for value in counts)
        mentions = re.search(rf"{number}\s*[个名位]?(?:{noun})", text)
        age_without_count = (
            key == "children_count" and "children_ages" in changed
            and base.get("children_count") is None
        )
        exceptions = re.findall(
            rf"(?:其中一间|另一间|另外一间|一间)(?:房)?(?:只有|只住|住|有|入住)?\s*({number})\s*[个名位]?(?:{noun})", text
        )
        if any((int(value) if value.isascii() else chinese_integer(value)) != draft.get(key)
               for value in exceptions):
            explicit = False
        # No-child statements apply to every room; positive counts need a
        # room-specific phrase. Do not silently carry single-room counts over.
        if key == "children_count" and draft.get(key) == 0 and not re.search(
            rf"{number}\s*[个名位]?(?:{noun})", text
        ):
            continue
        if not explicit and (room_changed or mentions or key in changed or age_without_count):
            draft[key] = None
            if key == "children_count":
                draft["children_ages"] = None


def describe_conditions(draft, today=None):
    today = today or china_today()
    missing = []
    for key, label in (
        ("place", "目的地"),
        ("place_type", "地点类型"),
        ("check_in", "入住日期"),
        ("check_out", "退房日期或晚数"),
        ("rooms", "房间数"),
        ("adults", "每房成人数"),
        ("children_count", "每房儿童人数"),
    ):
        if draft.get(key) is None:
            missing.append(label)
    count = draft.get("children_count")
    ages = draft.get("children_ages")
    if count and (ages is None or len(ages) != count):
        missing.append("每房全部儿童年龄")
    if draft.get("budget_fen") is not None and draft.get("budget_basis") != "room_night":
        missing.append("每房每晚预算口径")
    start, end = _date(draft.get("check_in")), _date(draft.get("check_out"))
    if start and start < today:
        missing.insert(0, "有效入住日期")
    if start and end:
        days = (end - start).days
        if not 1 <= days <= 28:
            missing.insert(0, "1至28晚的有效入住区间")
        elif draft.get("nights") is not None and days != draft["nights"]:
            missing.insert(0, "一致的退房日期与晚数")
    outside = outside_mainland_hint(draft.get("place"))
    if outside:
        missing.insert(0, "中国大陆目的地")
    query = None
    if not missing:
        try:
            query = SearchInput.model_validate(
                {
                    "place": draft["place"],
                    "place_type": draft["place_type"],
                    "check_in": draft["check_in"],
                    "check_out": draft["check_out"],
                    "adults": draft["adults"],
                    "rooms": draft["rooms"],
                    "children_ages": ages or [],
                    "budget_fen": draft["budget_fen"],
                    "limit": 20,
                },
                context={"today": today},
            )
        except ValidationError:
            missing.append("合法的日期和入住条件")
    question = (
        "条件已整理，请核对后填入查询表单。" if query else "请补充或确认：" + missing[0] + "。"
    )
    warnings = []
    if outside:
        question = "当前仅支持中国大陆住宿查询；请改为中国大陆目的地。"
        warnings.append("该目的地提示位于中国大陆范围之外，未生成可填表条件，也未请求酒店或地图。")
    if draft.get("notes"):
        warnings.append("附加条件尚未自动用于房价筛选，请核对并另外验证。")
    if draft.get("visits"):
        warnings.append(
            "计划前往地点仅来自你的描述，门店/地理位置尚未核实；"
            "只有你在表单明确选择餐厅多门店查找后才会请求候选。"
            "一次不能把多个地点自动合成为一条已核实路线。"
        )
    if draft.get("budget_basis") == "stay_total":
        warnings.append("当前房价查询只接受每房每晚参考预算，不能将整住期总预算直接套用。")
    if (draft.get("rooms") or 0) > 1 and (
        draft.get("adults") is None or draft.get("children_count") is None
    ):
        warnings.append("多间房需要确认每房成人和儿童人数；总人数不能直接用作每房人数。各房人数不同时，请按各房入住组合分别查询。")
    return query, missing, question, warnings
