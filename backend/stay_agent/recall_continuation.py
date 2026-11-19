"""Version-bound continuation state: identities and attempts only, never quote snapshots."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import copy
import threading
from uuid import uuid4

from .contracts import ApiError, RecallBranch, RecallCoverage, RecallResponse, SearchInput
from .map_recall import map_hotel_recall, BLOCKING
from .service import now_iso
from .storage import SessionError


class ContinuationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class State:
    request: object
    meta: dict
    hotels: dict
    progress: dict
    expires: float
    expires_at: str
    cursor: str | None
    revision: int = 0
    http_total: int = 0
    lock: object = field(default_factory=threading.Lock)


class RecallContinuation:
    def __init__(self, service):
        self.service = service
        self.states = {}

    @staticmethod
    def remaining(branches, attempted):
        return any(
            t.status == "not_queried" and t.name not in attempted
            for b in branches
            for t in b.hotel_targets
        )

    def remember(self, request, response, progress, sessions):
        if not self.remaining(response.branches, progress["attempted_names"]):
            return
        if response.error and response.error.code in BLOCKING:
            return
        svc = self.service
        with svc.lock:
            self.states = {
                key: value for key, value in self.states.items() if value.expires > svc.clock()
            }
            if len(self.states) >= 64:
                response.warnings.append("续查队列名额已满；本次未启用继续入口。")
                return
            try:
                absolute_expiry = sessions.ensure_live(response.request_id)
            except SessionError:
                return
            ttl = min(1800, max(0, absolute_expiry - sessions.clock()))
            cursor = uuid4().hex
            expires_at = datetime.fromtimestamp(absolute_expiry, timezone.utc).isoformat()
            response.continuation_cursor = cursor
            response.continuation_expires_at = expires_at
            self.states[response.request_id] = State(
                request=request.model_copy(deep=True),
                meta=response.model_dump(mode="json", exclude={"hotels"}),
                hotels={
                    h.id: h.model_dump(include={"id", "name", "address"}) for h in response.hotels
                },
                progress=copy.deepcopy(progress),
                expires=svc.clock() + ttl,
                expires_at=expires_at,
                cursor=cursor,
                http_total=response.recall_coverage.quote_http_requests,
            )

    def run(self, request, sessions):
        svc = self.service
        if not svc.capacity.acquire(blocking=False):
            raise ContinuationError("CONTINUATION_BUSY")
        state = None
        locked = False
        stats = None
        try:
            operation = svc._operation(request.operation_id, start=True)
            with svc.lock:
                state = self.states.get(request.request_id)
                if state is None:
                    raise ContinuationError("CONTINUATION_NOT_FOUND")
                if state.expires <= svc.clock():
                    self.states.pop(request.request_id, None)
                    raise ContinuationError("CONTINUATION_EXPIRED")
            locked = state.lock.acquire(blocking=False)
            if not locked:
                raise ContinuationError("CONTINUATION_BUSY")
            if state.cursor is None or request.continuation_cursor != state.cursor:
                raise ContinuationError("CONTINUATION_STALE")
            if operation.cancelled.is_set():
                raise ContinuationError("CANCELLED")
            try:
                sessions.ensure_live(request.request_id)
                SearchInput.model_validate(state.request.query.model_dump(mode="json"))
            except (SessionError, ValueError):
                raise ContinuationError("CONTINUATION_EXPIRED") from None
            deadline = min(svc.clock() + 60, state.expires)

            def interrupted():
                return (
                    "cancelled"
                    if operation.cancelled.is_set()
                    else "deadline"
                    if svc.clock() >= deadline
                    else None
                )

            branches = [RecallBranch.model_validate(b) for b in state.meta["branches"]]
            stats = RecallCoverage.model_validate(state.meta["recall_coverage"])
            stats.hotel_lookups_attempted = 0
            stats.quote_http_requests = 0
            stats.map_http_requests = 0
            stats.lookup_reuses = 0
            progress = copy.deepcopy(state.progress)
            # Reservations survive cancellation: a sent name is never automatically replayed.
            progress["attempted_names"] = state.progress["attempted_names"]
            results, error, refs, conflicts, progress = map_hotel_recall(
                svc,
                state.request,
                branches,
                {},
                None,
                stats,
                deadline,
                interrupted,
                None,
                resume=progress,
                cancelled=operation.cancelled,
            )
            progress["restaurant_ids"] = state.progress["restaurant_ids"]
            if operation.cancelled.is_set():
                raise ContinuationError("CANCELLED")
            if svc.clock() >= state.expires:
                raise ContinuationError("CONTINUATION_EXPIRED")
            if interrupted() == "deadline" and (error is None or error.code not in BLOCKING):
                error = ApiError(code="TIMEOUT", message="本轮续查达到60秒截止，已保留先前结果。")
            hotels = {ident: h for ident, h in state.hotels.items() if ident not in conflicts}
            delta = {}
            for branch, rows in results:
                branch.hotel_ids = [i for i in branch.hotel_ids if i not in conflicts]
                for hotel in rows:
                    if hotel.id in conflicts:
                        continue
                    if hotel.id not in hotels:
                        hotels[hotel.id] = hotel.model_dump(include={"id", "name", "address"})
                        delta[hotel.id] = hotel
                    if hotel.id not in branch.hotel_ids:
                        branch.hotel_ids.append(hotel.id)
            # Remove conflicts from branches with no successful new source response too.
            for branch in branches:
                branch.hotel_ids = [i for i in branch.hotel_ids if i in hotels]
            stats.identity_conflicts = len(conflicts)
            stats.identical_branch_sets = 0
            signatures = set()
            for branch in branches:
                if branch.hotel_ids:
                    signature = tuple(sorted(branch.hotel_ids))
                    stats.identical_branch_sets += signature in signatures
                    signatures.add(signature)
            stats.total_hotel_lookups = len(progress["attempted_names"])
            stats.total_quote_http_requests = state.http_total + stats.quote_http_requests
            remaining = self.remaining(branches, progress["attempted_names"])
            blocked = error is not None and error.code in BLOCKING
            cursor = uuid4().hex if remaining and not blocked else None
            meta = copy.deepcopy(state.meta)
            meta.update(
                operation_id=request.operation_id,
                revision=state.revision + 1,
                round_number=state.meta["round_number"] + 1,
                continuation_delta=True,
                continuation_cursor=cursor,
                continuation_expires_at=state.expires_at,
                removed_hotel_ids=sorted(set(state.hotels) - set(hotels)),
                retained_total=len(hotels),
                attempted_hotel_names=sorted(progress["attempted_names"]),
                queried_at=now_iso(),
                branches=[b.model_dump(mode="json") for b in branches],
                recall_coverage=stats.model_dump(),
                hotel_observations=[
                    {"hotel_id": i, "queried_at": progress["observations"][i]} for i in hotels
                ],
                error=error.model_dump() if error else None,
            )
            incomplete = (
                not stats.discovery_complete
                or any(b.status != "complete" for b in branches)
                or bool(conflicts)
                or interrupted() is not None
                or error is not None
            )
            meta["status"] = "partial" if incomplete else "complete"
            meta["coverage"] = {
                **meta["coverage"],
                "returned_count": len(delta),
                "limited": incomplete,
            }
            warning = "续查只新增本轮候选；旧参考价保留原时间，地图队列不是新点位或路线。"
            if warning not in meta["warnings"]:
                meta["warnings"].append(warning)
            response = RecallResponse(hotels=list(delta.values()), **meta)
            with svc.lock:
                if operation.cancelled.is_set():
                    raise ContinuationError("CANCELLED")
                if svc.clock() >= state.expires:
                    raise ContinuationError("CONTINUATION_EXPIRED")
                try:
                    sessions.replace_membership(
                        request.request_id, list(hotels), list(hotels.values())
                    )
                except SessionError:
                    raise ContinuationError("CONTINUATION_EXPIRED") from None
                registered = False
                if hotels and (
                    request.request_id in svc.associations or len(svc.associations) < 64
                ):
                    svc.associations[request.request_id] = {
                        "expires": state.expires,
                        "provider": state.request.provider,
                        "query": state.request.query.model_dump(mode="json"),
                        "hotels": hotels,
                        "branches": [
                            {
                                **b.model_dump(
                                    include={"id", "name", "address", "city", "hotel_ids"}
                                ),
                                "source_id": progress["restaurant_ids"][b.id],
                                "hotel_targets": refs.get(b.id, {}),
                            }
                            for b in branches
                            if b.hotel_ids
                        ],
                    }
                    registered = True
                elif not hotels:
                    svc.associations.pop(request.request_id, None)
                response.walk_check_available = registered
                state.meta = response.model_dump(mode="json", exclude={"hotels"})
                state.hotels = hotels
                state.progress = progress
                state.cursor = cursor
                state.revision += 1
            return response
        finally:
            if state is not None and locked:
                if stats is not None:
                    state.http_total += stats.quote_http_requests
                state.lock.release()
            svc.capacity.release()
