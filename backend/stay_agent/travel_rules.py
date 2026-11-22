"""Conservative identity assessment and exact current-fare projection."""

from decimal import Decimal, InvalidOperation
import re
import unicodedata

# Annotations a map provider appends that carry no doorplate information:
# parenthetical notes such as "(东漖北路口)" and explicit facility descriptions
# such as "A口步行330米" or "3楼前台". Only these known patterns are removed;
# doorplate numbers, delimiters, room numbers and branch wording stay intact.
_PAREN = re.compile(r"\(([^()]{1,40})\)")
_WALK_NOTE = r"[a-z]?\d*口?步行\d+(?:\.\d+)?米"
_FRONT_DESK = r"\d+(?:楼|层)前台"
_NOISE_SUFFIX = re.compile(rf"(?:{_WALK_NOTE}|{_FRONT_DESK})$")
_WAYFINDING = re.compile(rf"(?:[^()]+路口|[^()]*{_WALK_NOTE})")
_BRANCH_LABEL = re.compile(r"([^·()]+)(?:·([^·()]+)|\(([^·()]+)\))")
_LOCATOR = re.compile(
    r"((?:地上|地下|负)?[a-z]?[0-9零〇一二三四五六七八九十百两]+"
    r"(?:[-至][0-9零〇一二三四五六七八九十百两]+)?|[a-z])"
    r"(号楼|单元|栋|幢|座|楼|层|房|室|号(?!线|口))"
)


def normalized(value):
    return "".join(
        c for c in unicodedata.normalize("NFKC", value or "").casefold() if not c.isspace()
    )


def _without_wayfinding(match):
    note = match[1]
    if re.fullmatch(_FRONT_DESK, note):
        return ""
    if _WAYFINDING.fullmatch(note) and not _LOCATOR.search(note):
        return ""
    return match[0]


def address_core(value):
    """Address body with only the known non-doorplate annotations removed."""
    text = normalized(value)
    for _ in range(3):
        before = text
        text = _PAREN.sub(_without_wayfinding, text)
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


def address_locators_agree(left, right):
    """Geocoding can check the street location, without resolving a floor or unit."""
    def parts(address):
        doors, rooms = set(), set()
        for number, kind in _LOCATOR.findall(address_core(address)):
            if kind == "号":
                doors.add(number)
            else:
                rooms.add((number, "楼" if kind == "层" else kind))
        return doors, rooms

    left_doors, left_rooms = parts(left)
    right_doors, right_rooms = parts(right)
    return left_rooms == right_rooms and (
        not left_doors or not right_doors or left_doors == right_doors
    )


def city_name(value):
    text = normalized(value)
    return text[:-1] if text.endswith("市") else text


def _branch_parts(name):
    match = _BRANCH_LABEL.fullmatch(name)
    if match:
        return match[1], match[2] or match[3]
    return None


def checks(hotel, candidate, original_city, requested_city, stay_city_basis=None):
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
    address = address_matches(hotel.get("address"), candidate.get("address"))
    stay_name = normalized(hotel.get("name"))
    map_name = normalized(candidate.get("name"))
    name = stay_name == map_name if stay_name and map_name else None
    if name is False and address is True and city is True:
        # Suppliers may render the same complete branch label as "酒店·分店" or "酒店(分店)".
        # Only use this equivalence when the address and city already agree.
        stay_branch = _branch_parts(stay_name)
        name = stay_branch is not None and stay_branch == _branch_parts(map_name)
    return {"name_match": name, "address_match": address, "city_match": city}


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
