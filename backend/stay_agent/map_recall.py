"""Map-first recall under a shared source budget; no proximity or identity is assumed."""

from uuid import uuid4

from .contracts import RecallHotelTarget
from .travel import TravelService
from .travel_rules import assessment

BLOCKING = {
    "AUTHENTICATION_FAILED",
    "ACCESS_DENIED",
    "SOURCE_DAILY_LIMIT",
    "RATE_LIMITED",
    "REQUEST_LIMIT",
    "MISSING_CREDENTIAL",
    "INVALID_CREDENTIAL",
    "CONFIGURATION_MISSING",
    "PROVIDER_REJECTED",
}


class NoSearchStore:
    """Intermediate supplier search IDs and rejected hotel metadata are not persisted."""

    def save(self, *args, **kwargs):
        pass


def map_hotel_recall(
    service,
    request,
    branches,
    points,
    maps,
    stats,
    deadline,
    interrupted,
    error,
    resume=None,
    cancelled=None,
):
    selected = [b for b in branches if b.query_place][: request.branch_limit]
    queues = {}
    refs = {}
    metadata = {}
    successes = set()
    failed = set()
    raw_ids = {}
    accepted = {}
    identities = {}
    conflicts = set()
    anchors = {}
    attempted_names = set()
    observations = {}
    cache = {}
    client = None
    halted = None
    # All selected branches get one bounded neighborhood discovery before price calls.
    for branch in selected if resume is None else []:
        stop = interrupted()
        if stop or halted:
            branch.reason = stop or halted
            continue
        stats.nearby_branches_attempted += 1
        try:
            nearby = maps.around(points[branch.id], "酒店", limit=10)
            if interrupted():
                branch.reason = interrupted()
                continue
            seen = set()
            rows = []
            for point in nearby:
                data = maps.metadata.get(point.poi_id, {})
                address, city = data.get("address"), data.get("city")
                if (
                    point.poi_id in seen
                    or not point.title
                    or len(point.title) > 120
                    or not isinstance(address, str)
                    or not 0 < len(address) <= 300
                    or city
                    not in (request.query.place, request.query.place.removesuffix("市") + "市")
                    or any(
                        word in point.title
                        for word in ("停车", "大堂", "餐厅", "酒吧", "健身", "入口", "出口")
                    )
                ):
                    stats.nearby_invalid += 1
                    continue
                seen.add(point.poi_id)
                target = RecallHotelTarget(
                    id=uuid4().hex, name=point.title, address=address, city=city
                )
                branch.hotel_targets.append(target)
                rows.append(target)
                metadata[target.id] = {
                    "source_id": point.poi_id,
                    **target.model_dump(include={"id", "name", "address", "city"}),
                }
            queues[branch.id] = rows
            branch.nearby_status = "complete"
            branch.limited = len(nearby) >= 10
            branch.reason = None
            if not rows:
                successes.add(branch.id)
                branch.reason = "no_valid_map_hotels"
        except Exception as failure:
            branch.nearby_status = "unavailable"
            branch.status = "unavailable"
            branch.reason = "nearby_source_failure"
            branch.error = TravelService.error(failure)
            error = branch.error
            if branch.error.code in BLOCKING:
                halted = "map_source_blocked"

    if resume is not None:
        metadata = resume["metadata"]
        attempted_names = resume["attempted_names"]
        identities = resume["identities"]
        conflicts = resume["conflicts"]
        anchors = resume["anchors"]
        raw_ids = resume["raw_ids"]
        observations = resume["observations"]
        queues = {b.id: b.hotel_targets for b in selected}
        successes = {b.id for b in selected if b.status in ("complete", "partial")}
        failed = {b.id for b in selected if any(t.status == "unavailable" for t in b.hotel_targets)}
    round_successes = 0
    # Per-depth round robin: no branch consumes all five distinct-name lookups first.
    halted = None
    for depth in range(max((len(q) for q in queues.values()), default=0)):
        for branch in selected:
            queue = queues.get(branch.id, [])
            if depth >= len(queue):
                continue
            target = queue[depth]
            if target.status != "not_queried":
                continue
            stop = interrupted()
            if stop or halted:
                target.reason = stop or halted
                continue
            response = cache.get(target.name)
            if response is None:
                if target.name in attempted_names:
                    target.status = "unavailable"
                    target.reason = "attempt_interrupted"
                    failed.add(branch.id)
                    continue
                if stats.hotel_lookups_attempted >= 5:
                    target.reason = "lookup_budget"
                    continue
                stats.hotel_lookups_attempted += 1
                attempted_names.add(target.name)
                try:
                    if client is None:
                        client = service.quote_factory(deadline)
                    client.cancelled = (
                        cancelled
                        if cancelled is not None
                        else getattr(getattr(maps, "http", None), "cancelled", None)
                    )
                    query = request.query.model_copy(
                        update={"place": target.name, "place_type": "酒店"}
                    )
                    # Keep the documented hotel-name place and placeType.
                    # originQuery carries the missing city and map address only as
                    # context; provider behavior must still be verified live.
                    hint = f"地图候选城市：{request.query.place}；地图候选地址：{target.address}"
                    response = service.quotes.search(
                        query, NoSearchStore(), client=client, origin_hint=hint
                    )
                    if interrupted():
                        target.reason = interrupted()
                        continue
                    cache[target.name] = response
                    for hotel in response.hotels:
                        identity = (hotel.name, hotel.address)
                        previous = identities.setdefault(hotel.id, identity)
                        if previous != identity:
                            conflicts.add(hotel.id)
                except Exception as failure:
                    target.status = "unavailable"
                    target.reason = "quote_source_blocked"
                    branch.error = error = TravelService.error(failure)
                    failed.add(branch.id)
                    halted = "quote_source_blocked"
                    continue
            else:
                stats.lookup_reuses += 1
            if branch.id not in raw_ids:
                stats.queried_branches += 1
                raw_ids[branch.id] = set()
            branch.queried_at = response.queried_at
            target.source_count = len(response.hotels)
            raw_ids[branch.id].update(h.id for h in response.hotels)
            branch.limited = branch.limited or response.coverage.limited
            if response.status == "unavailable":
                target.status = "unavailable"
                branch.error = error = response.error
                failed.add(branch.id)
                if response.error and response.error.code in BLOCKING:
                    halted = "quote_source_blocked"
                continue
            successes.add(branch.id)
            round_successes += 1
            if response.status == "partial":
                failed.add(branch.id)
            if not response.hotels:
                target.status = "empty"
                continue
            # Exact complete identity wins; otherwise only a unique exact name/address
            # candidate may remain for explicit later human review.
            option = target.model_dump(include={"id", "name", "address", "city"})
            exact = []
            possible = []
            for hotel in response.hotels:
                # The supplier projection has no independent city field.
                # The requested city cannot stand in for the hotel source city.
                _, match = assessment(
                    hotel.model_dump(), [option], None, request.query.place
                )
                if match["status"] == "matched":
                    exact.append(hotel)
                if hotel.name == target.name or hotel.address == target.address:
                    possible.append(hotel)
            candidates = exact if exact else possible
            stats.excluded_source_hotels += len(response.hotels) - len(candidates)
            if len(candidates) != 1:
                target.status = "ambiguous" if candidates else "excluded"
                target.reason = "multiple_source_identities" if candidates else "other_source_hotel"
                continue
            hotel = candidates[0]
            anchor = (metadata[target.id]["source_id"], target.name, target.address, target.city)
            previous = anchors.setdefault(hotel.id, anchor)
            if previous != anchor:
                conflicts.add(hotel.id)
            target.status = "retained"
            target.hotel_id = hotel.id
            target.match_status = "matched" if exact else "unverified"
            if not exact:
                target.reason = (
                    "source_city_unverified"
                    if hotel.name == target.name and hotel.address == target.address
                    else "source_address_mismatch"
                    if hotel.name == target.name
                    else "source_name_mismatch"
                )
            observations.setdefault(hotel.id, response.queried_at)
            accepted.setdefault(branch.id, []).append(hotel)
            refs.setdefault(branch.id, {})[hotel.id] = metadata[target.id]

    if client is not None:
        stats.quote_http_requests = client.request_count
    results = []
    stats.unqueried_targets = 0
    stats.total_hotel_lookups = len(attempted_names)
    for branch in selected:
        for target in branch.hotel_targets:
            if target.hotel_id in conflicts:
                target.hotel_id = None
                target.match_status = None
                target.status = "ambiguous"
                target.reason = "identity_conflict"
            if target.status == "not_queried" and not target.reason:
                target.reason = interrupted() or halted or "lookup_budget"
        pending = any(t.status == "not_queried" for t in branch.hotel_targets)
        stats.unqueried_targets += sum(t.status == "not_queried" for t in branch.hotel_targets)
        branch.returned_count = len(raw_ids.get(branch.id, ()))
        if branch.id in successes:
            branch.status = (
                "partial" if pending or branch.limited or branch.id in failed else "complete"
            )
            if pending:
                branch.reason = interrupted() or halted or "lookup_budget"
            rows = [h for h in accepted.get(branch.id, []) if h.id not in conflicts]
            results.append((branch, rows))
        elif branch.id in failed or branch.nearby_status == "unavailable":
            branch.status = "unavailable"
        elif branch.hotel_targets:
            branch.reason = interrupted() or halted or "lookup_budget"
        refs[branch.id] = {
            t.hotel_id: metadata[t.id]
            for t in branch.hotel_targets
            if t.hotel_id is not None and t.hotel_id not in conflicts
        }
    progress = {
        "metadata": metadata,
        "attempted_names": attempted_names,
        "identities": identities,
        "conflicts": conflicts,
        "anchors": anchors,
        "raw_ids": raw_ids,
        "observations": observations,
        "round_successes": round_successes,
    }
    return results, error, refs, conflicts, progress
