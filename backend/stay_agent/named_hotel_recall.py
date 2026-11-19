"""User-nominated hotel recall: fresh independent map identity before one supplier lookup.

This mode intentionally bypasses around(limit=10), not map/source identity checks.
No automatic pagination, alternate names, second supplier lookup or real calls on import.
"""
from unicodedata import normalize
from uuid import uuid4

from .contracts import RecallHotelTarget
from .map_recall import NoSearchStore
from .travel import TravelService


def identity(value):
    # Keep punctuation boundaries (hotel brand "·", door range "-",
    # and floor/unit delimiters). NFKC only unifies full-width forms.
    value = normalize("NFKC", value or "").casefold()
    value = value.replace("–", "-").replace("—", "-").replace("－", "-")
    return "".join(char for char in value if not char.isspace())


def same_address(first, second):
    """Allow city/district prefix differences, not missing house or unit characters."""
    a, b = identity(first), identity(second)
    return min(len(a), len(b)) >= 8 and (a in b or b in a)


def same_identity(first_name, first_address, second_name, second_address):
    return (
        bool(identity(first_name))
        and identity(first_name) == identity(second_name)
        and same_address(first_address, second_address)
    )


def city_matches(value, selected_city):
    return identity(value).removesuffix("市") == identity(selected_city).removesuffix("市")


def hotel_category(meta, provider):
    """Independent map POI category is required; name alone is insufficient."""
    if provider == "amap":
        code = meta.get("typecode")
        group = meta.get("type") or meta.get("category")
        return (
            isinstance(code, str) and code.startswith("10")
            and isinstance(group, str) and "住宿服务" in group
        )
    group = meta.get("category")
    return isinstance(group, str) and any(
        part in group for part in ("住宿", "酒店", "旅馆", "公寓", "民宿")
    )


def qualify_restaurant(branch, request):
    return (
        branch.role == "restaurant_body"
        and city_matches(branch.city, request.query.place)
        and same_identity(
            branch.name, branch.address, request.restaurant_keyword,
            request.restaurant_address,
        )
    )


def named_hotel_recall(service, request, branches, points, maps, stats, deadline,
                       interrupted, cancelled):
    """Return the usual recall tuple while issuing at most one hotel map search
    and one supplier business lookup, with the same 60s shared recall deadline.
    """
    for branch in branches:
        if branch.query_place and not qualify_restaurant(branch, request):
            branch.reason = "named_restaurant_identity_mismatch"
            branch.query_place = None
    eligible = [branch for branch in branches if branch.query_place]
    if len(eligible) != 1:
        for branch in eligible:
            branch.query_place = None
            branch.reason = "named_restaurant_not_unique"
        return [], None, {}, set(), None

    branch = eligible[0]
    requested = request.named_hotel
    assert requested is not None  # Pydantic contract requires this mode's identity
    target = RecallHotelTarget(
        id=uuid4().hex, name=requested.name, address=requested.address,
        city=request.query.place, reason="named_map_not_queried",
    )
    branch.hotel_targets.append(target)
    if interrupted():
        target.reason = interrupted()
        return [], None, {}, set(), None

    stats.named_hotel_map_lookups += 1
    try:
        matches = maps.search(requested.name, request.query.place, page=1, page_size=10)
        if interrupted():
            target.reason = interrupted()
            return [], None, {}, set(), None
    except Exception as failure:
        branch.nearby_status = "unavailable"
        branch.status = "unavailable"
        branch.reason = target.reason = "named_hotel_map_unavailable"
        branch.error = TravelService.error(failure)
        return [], branch.error, {}, set(), None

    branch.nearby_status = "complete"  # direct named map search, not around()
    verified = []
    for point in matches:
        meta = maps.metadata.get(point.poi_id, {})
        if (
            same_identity(point.title, meta.get("address"), requested.name, requested.address)
            and city_matches(meta.get("city"), request.query.place)
            and hotel_category(meta, request.provider)
        ):
            verified.append(point)
    stats.named_hotel_map_matches = len(verified)
    if len(verified) != 1 or maps.last_search.get("raw_count", 0) >= 10:
        target.status = "ambiguous" if len(verified) > 1 else "excluded"
        target.reason = (
            "named_map_ambiguous" if len(verified) > 1
            else "named_map_page_limited" if maps.last_search.get("raw_count", 0) >= 10
            else "named_map_identity_not_found"
        )
        branch.reason = target.reason
        branch.status = "complete"
        return [(branch, [])], None, {}, set(), None

    point = verified[0]
    metadata = maps.metadata.get(point.poi_id, {})
    # The requested address identifies a lead, the map's returned address is the anchor.
    # Never use a user-entered address as independent map or supplier proof.
    target.name = point.title
    target.address = metadata["address"]
    target.city = metadata["city"]
    stats.queried_branches += 1
    stats.hotel_lookups_attempted += 1
    stats.total_hotel_lookups = 1
    target.reason = "named_supplier_lookup_reserved"
    try:
        if interrupted():
            target.reason = interrupted()
            return [], None, {}, set(), None
        client = service.quote_factory(deadline)
        client.cancelled = cancelled
        query = request.query.model_copy(
            update={"place": requested.name, "place_type": "酒店"}
        )
        hint = f"地图候选城市：{request.query.place}；地图候选地址：{target.address}"
        response = service.quotes.search(query, NoSearchStore(), client=client, origin_hint=hint)
        stats.quote_http_requests = client.request_count
        stats.total_quote_http_requests = client.request_count
        if interrupted():
            target.reason = interrupted()
            return [], None, {}, set(), None
    except Exception as failure:
        target.status = "unavailable"
        target.reason = branch.reason = "named_supplier_unavailable"
        branch.status = "unavailable"
        branch.error = TravelService.error(failure)
        return [], branch.error, {}, set(), None

    branch.queried_at = response.queried_at
    branch.returned_count = len(response.hotels)
    target.source_count = len(response.hotels)
    branch.limited = response.coverage.limited
    if response.status == "unavailable":
        target.status = "unavailable"
        target.reason = "named_supplier_unavailable"
        branch.reason = target.reason
        branch.status = "unavailable"
        branch.error = response.error
        return [], response.error, {}, set(), None

    # Source may lack an independently verified city; exact full name and address
    # still leave an explicit human-confirmation gate in BranchWalkService.
    strict = [
        hotel for hotel in response.hotels
        if same_identity(hotel.name, hotel.address, point.title, target.address)
        and same_identity(hotel.name, hotel.address, requested.name, requested.address)
    ]
    stats.excluded_source_hotels += len(response.hotels) - len(strict)
    branch.status = "partial" if response.status == "partial" else "complete"
    if len(strict) != 1:
        target.status = "ambiguous" if strict else "excluded"
        target.reason = (
            "named_supplier_ambiguous" if strict else "named_supplier_identity_mismatch"
        )
        branch.reason = target.reason
        return [(branch, [])], None, {}, set(), None

    hotel = strict[0]
    target.status = "retained"
    target.match_status = "unverified"
    target.hotel_id = hotel.id
    target.reason = "source_city_unverified"
    branch.reason = None
    reference = {
        "source_id": point.poi_id,
        "id": target.id,
        "name": target.name,
        "address": target.address,
        "city": target.city,
    }
    progress = {
        "attempted_names": {requested.name},
        "observations": {hotel.id: response.queried_at},
    }
    return [(branch, [hotel])], None, {branch.id: {hotel.id: reference}}, set(), progress
