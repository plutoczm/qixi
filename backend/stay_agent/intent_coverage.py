"""Conservative checks for explicit, high-confidence changes missed by a model.

This is deliberately not general natural-language understanding. Ambiguous phrases
are left for the user's draft review rather than guessed by deterministic code.
"""
from decimal import Decimal
import re

from .model_output import ProposalError

# A bounded set of unambiguous city names; do not infer arbitrary POI names.
_CITIES = (
    "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "武汉",
    "西安", "南京", "苏州", "天津", "长沙", "郑州", "厦门", "青岛",
    "济南", "福州", "合肥", "南昌", "昆明", "贵阳", "南宁", "海口",
    "三亚", "太原", "兰州", "银川", "西宁", "拉萨", "哈尔滨",
    "沈阳", "长春", "大连", "宁波", "无锡", "佛山", "东莞",
)
_CITY_PATTERN = re.compile(
    r"(?:改去|改到|改成|换到|换成|改为|还是)\s*("
    + "|".join(sorted(_CITIES, key=len, reverse=True))
    + r")(?:市)?(?=$|[，,。；;、\s\d]|住|要|待)"
)
_ROOMS_PATTERN = re.compile(r"([1-4一二两三四])\s*(?:间房|间客房|个房间)")
_ROOM_COUNT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4}
_BUDGET_PATTERN = re.compile(
    r"(?:每房每晚|每间房每晚|每房/晚)\s*([0-9]{1,7}(?:\.[0-9]{1,2})?)\s*(?:元|块)"
)
_BUDGET_CLEAR_PATTERN = re.compile(
    r"不限预算|预算不限|取消预算|不要预算限制|不设预算|预算不限制"
)


def _negated(message: str, start: int, end: int) -> bool:
    before = message[max(0, start - 5):start]
    after = message[end:end + 3]
    return bool(
        re.search(r"(?:不想要|不需要|不要|不想|不用|无需|无须|不再|别|取消|不是|避免|不)\s*$", before)
        or re.search(r"(?:只剩|剩余|共有|仅有|库存|还有)\s*$", before)
        or re.match(r"(?:不要了|不用了|不要|不用)", after)
    )


def require_explicit_intent_coverage(latest_input: str, draft: dict) -> None:
    """Reject only plainly detectable omissions; never invent a proposed value."""
    city_matches = [
        match for match in _CITY_PATTERN.finditer(latest_input)
        if not _negated(latest_input, match.start(), match.end())
    ]
    if city_matches:
        desired = city_matches[-1].group(1)
        actual = draft.get("place")
        if not isinstance(actual, str) or actual.removesuffix("市") != desired:
            raise ProposalError("SEMANTIC_OMISSION")
        if draft.get("place_type") != "城市":
            raise ProposalError("SEMANTIC_OMISSION")

    # Explicit positive restaurant intent is a first-class visit, not an
    # arbitrary hotel destination. This is a narrow omission guard for the
    # well-known brand case; the LLM schema handles other named destinations.
    if (
        "海底捞" in latest_input
        and re.search(
            r"去海底捞|海底捞.{0,12}吃|海底捞.{0,12}(?:附近住|周边住|附近找酒店)"
            r"|(?:住在|住到|住|酒店).{0,10}海底捞.{0,8}(?:附近|周边|旁边)",
            latest_input,
        )
        and not re.search(
            r"(?:不|不要|不想|无需|别|取消).{0,9}海底捞"
            r"|海底捞.{0,8}(?:不去|不考虑|取消)",
            latest_input,
        )
    ):
        if not any(
            item.get("category") == "餐饮" and "海底捞" in item.get("name", "")
            for item in draft.get("visits", [])
        ):
            raise ProposalError("SEMANTIC_OMISSION")

    # A question such as '两间房还是一间房？' is not a confirmed room count.
    room_matches = (
        [
            match for match in _ROOMS_PATTERN.finditer(latest_input)
            if not _negated(latest_input, match.start(), match.end())
        ]
        if "?" not in latest_input and "？" not in latest_input else []
    )
    if room_matches:
        value = room_matches[-1].group(1)
        expected = _ROOM_COUNT.get(value, int(value) if value.isascii() else -1)
        if draft.get("rooms") != expected:
            raise ProposalError("SEMANTIC_OMISSION")

    budget_actions = [
        (match.start(), None) for match in _BUDGET_CLEAR_PATTERN.finditer(latest_input)
    ]
    for match in _BUDGET_PATTERN.finditer(latest_input):
        if not _negated(latest_input, match.start(), match.end()):
            budget_actions.append((match.start(), int(Decimal(match.group(1)) * 100)))
    if budget_actions:
        budget_actions.sort(key=lambda action: action[0])
        latest_budget = budget_actions[-1][1]
        if draft.get("budget_fen") != latest_budget:
            raise ProposalError("SEMANTIC_OMISSION")
        if latest_budget is not None and draft.get("budget_basis") != "room_night":
            raise ProposalError("SEMANTIC_OMISSION")
