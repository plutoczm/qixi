"""Conservative identity assessment and exact current-fare projection."""

from decimal import Decimal, InvalidOperation
import re
import unicodedata

# Annotations a map provider appends that carry no doorplate information:
# parenthetical notes such as "(东漖北路口)" and explicit facility descriptions
# such as "A口步行330米" or "3楼前台". Only these known patterns are removed;
# doorplate numbers, delimiters, room numbers and branch wording stay intact.
_PAREN = re.compile(r"\([^()]{1,40}\)")
_NOISE_SUFFIX = re.compile(r"(?:[A-Za-z]?\d*口?步行\d+(?:\.\d+)?米|\d+楼前台|\d+层前台)")


def normalized(value):
    return "".join(
        c for c in unicodedata.normalize("NFKC", value or "").casefold() if not c.isspace()
    )


def address_core(value):
    """Address body with only the known non-doorplate annotations removed."""
    text = normalized(value)
    for _ in range(3):
        before = text
        text = _PAREN.sub("", text)
        text = _NOISE_SUFFIX.sub("", text)
        if text == before:
            break
    return text


def address_matches(left, right):
    if not left or not right:
        return None
    left_core, right_core = address_core(left), address_core(right)
    if not left_core or not right_core:
        return None
    return left_core == right_core


def city_name(value):
    text = normalized(value)
    return text[:-1] if text.endswith("市") else text


def checks(hotel, candidate, original_city, requested_city, stay_city_basis=None):
    def compare(left, right):
        return normalized(left) == normalized(right) if left and right else None

    # The stay source has no city field of its own. `stay_city_basis` is only
    # passed by the caller that KNOWS the stay was returned by a search issued
    # for that city (the places flow). Callers that recovered the stay from a
    # map point must not pass it: there the requested city would be a guess and
    # identity has to stay unverified with source_city_unverified.
    base_city = original_city or stay_city_basis
    city = (
        city_name(base_city) == city_name(requested_city) == city_name(candidate.get("city"))
        if base_city and requested_city and candidate.get("city")
        else None
    )
    return {
        "name_match": compare(hotel.get("name"), candidate.get("name")),
        "address_match": address_matches(hotel.get("address"), candidate.get("address")),
        "city_match": city,
    }


def assessment(hotel, candidates, original_city, requested_city, stay_city_basis=None):
    entries = [
        {
            **candidate,
            **checks(hotel, candidate, original_city, requested_city, stay_city_basis),
        }
        for candidate in candidates
    ]
    matched = [
        c
        for c in entries
        if c["name_match"] is True and c["address_match"] is True and c["city_match"] is True
    ]
    status = (
        "matched"
        if len(matched) == 1
        else "ambiguous"
        if len(matched) > 1
        else "unverified"
        if entries
        else "not_found"
    )
    relaxed = any(
        normalized(hotel.get("address")) != normalized(c.get("address")) for c in matched
    )
    return entries, {
        "status": status,
        "selected_id": matched[0]["id"] if status == "matched" else None,
        "basis": ("address_core" if relaxed else "exact") if status == "matched" else None,
        "explanation": "名称一致、地址主干一致（已忽略地图来源的补充说明）且城市一致，唯一命中。"
        if status == "matched" and relaxed
        else "名称、地址、城市均一致且唯一命中。"
        if status == "matched"
        else "多条候选字段一致，请核对具体房源。"
        if status == "ambiguous"
        else "未取得可唯一确认同店的完整证据，请核对名称和地址。"
        if entries
        else "本次范围未取得住宿地点。",
    }


def entrance_name(name):
    return any(word in name for word in ("出入口", "出口", "入口")) or bool(
        re.search(r"[A-Z][0-9]*口", name)
    )


def fare_amount(text):
    if type(text) not in (str, int, float):
        return None, None
    value = str(text).strip()
    match = re.fullmatch(
        r"(?:[¥￥]\s*)?(\d+(?:\.\d{1,2})?)(?:\s*[-~～–至]\s*(?:[¥￥]\s*)?(\d+(?:\.\d{1,2})?))?\s*元?",
        value,
    )
    if not match:
        return None, None
    try:
        low = int(Decimal(match[1]) * 100)
        high = int(Decimal(match[2] or match[1]) * 100)
        return (low, high) if 0 <= low <= high <= 100000000 else (None, None)
    except (InvalidOperation, ValueError, OverflowError):
        return None, None
