"""Three-source search with source-local sessions and a shared cutoff."""

from dataclasses import dataclass, field
import sqlite3
import threading
import time
from typing import Literal

from pydantic import Field

from .contracts import DiscoveryCandidate, Model, SearchInput, SearchResponse
from .flyai_source import parallel_flyai_adapter
from .multi_source import SAFE_CODES, MultiSourceOrchestrator, MultiSourceRequest, SourceResult, SourceStatus
from .storage import SessionError

SOURCE_IDS = ("rollinggo", "tuniu", "flyai")
QUOTE_BUDGET = 5


class ParallelSearchRequest(Model):
    operation_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")
    query: SearchInput


class ParallelSourceResult(Model):
    id: str
    status: SourceStatus
    http_used: int
    observed_at: str | None
    unavailable_reason: str | None
    search: SearchResponse | None
    candidates: list[DiscoveryCandidate]

    @classmethod
    def from_result(cls, result):
        return cls(
            **result.model_dump(exclude={"candidates", "search"}), search=result.search,
            candidates=[
                DiscoveryCandidate(name=hint.name, address=hint.address, price_text=hint.price_text,
                                   star=hint.star, nearby_transit=hint.nearby_transit,
                                   coordinates_present=hint.coordinates_present)
                for hint in result.candidates
            ],
        )


class ParallelSearchResponse(Model):
    operation_id: str
    status: Literal["complete", "partial", "cancelled"]
    context: SearchInput
    sources: list[ParallelSourceResult]


class PendingSession:
    """The supplier worker can stage one membership. Only the coordinator publishes it."""

    def __init__(self, store):
        self.clock = store.clock
        self.expiry = self.clock() + store.ttl_seconds
        self.row = None

    def save(self, request_id, context, hotel_ids, hotel_metadata=None, *, supplier_source):
        if self.row is not None:
            raise SessionError("SESSION_ALREADY_STAGED")
        self.row = (request_id, context, hotel_ids, hotel_metadata or [], supplier_source)

    def ensure_live(self, request_id):
        if self.row is None or self.row[0] != request_id:
            raise SessionError("SEARCH_NOT_FOUND")
        return self.expiry

    def list_hotels(self, request_id):
        self.ensure_live(request_id)
        _, query, ids, hotels, source = self.row
        return {**query, "_supplier_source": source}, [hotel for hotel in hotels if hotel["id"] in ids]

    def publish(self, response, store, service):
        self.ensure_live(response.request_id)
        request_id, context, ids, hotels, source = self.row
        if source != response.source.id or context != response.context.model_dump(mode="json"):
            raise SessionError("SEARCH_CONTEXT_MISMATCH")
        store.save(request_id, context, ids, hotels, supplier_source=source)
        registry = getattr(service, "city_observations", None)
        if registry is not None and response.context.poi_name:
            registry.publish(request_id, self, store)


@dataclass
class SearchOperation:
    expires: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    request_ids: tuple[str, ...] = ()
    query: SearchInput | None = None
    discovery_hotels: tuple[tuple[str, str | None], ...] = ()


class ParallelSearch:
    def __init__(self, rollinggo, tuniu, flyai=None, *, deadline_seconds=60):
        self.services = {"rollinggo": rollinggo, "tuniu": tuniu}
        self.flyai = flyai or parallel_flyai_adapter
        self.deadline_seconds = deadline_seconds
        self._lock = threading.Lock()
        self._operations: dict[str, SearchOperation] = {}

    def _operation(self, operation_id):
        now = time.monotonic()
        self._operations = {
            key: operation for key, operation in self._operations.items() if operation.expires > now
        }
        operation = self._operations.get(operation_id)
        if operation is None:
            if len(self._operations) >= 256:
                raise SessionError("PARALLEL_SEARCH_BUSY")
            operation = self._operations[operation_id] = SearchOperation(now + 1800)
        return operation

    def cancel(self, operation_id):
        with self._lock:
            self._operation(operation_id).cancelled.set()

    def close(self):
        with self._lock:
            for operation in self._operations.values():
                operation.cancelled.set()

    def nearby_requests(self, operation_id):
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None or operation.cancelled.is_set() or operation.expires <= time.monotonic():
                raise SessionError("SEARCH_EXPIRED")
            return operation.request_ids

    def discovery_hotel(self, operation_id, hotel_id):
        with self._lock:
            operation = self._operations.get(operation_id)
            if (operation is None or operation.query is None
                or operation.cancelled.is_set() or operation.expires <= time.monotonic()):
                raise SessionError("SEARCH_EXPIRED")
            if not 1 <= hotel_id <= len(operation.discovery_hotels):
                raise SessionError("HOTEL_NOT_IN_SEARCH")
            name, address = operation.discovery_hotels[hotel_id - 1]
            return operation.query, {"name": name, "address": address}

    def _quote_adapter(self, source, pending):
        def adapter(query, context):
            if source == "tuniu" and query.place_type != "城市":
                return SourceResult(id=source, status="unavailable",
                                    unavailable_reason="UNSUPPORTED_PLACE_TYPE")
            search = self.services[source].search(query, pending, source_context=context)
            if search.source.id != source:
                return SourceResult(id=source, status="unavailable", unavailable_reason="SOURCE_ID_MISMATCH")
            if search.status == "unavailable":
                code = search.error.code if search.error else "SOURCE_ERROR"
                status = "unconfigured" if code == "CONFIGURATION_MISSING" else "unavailable"
                return SourceResult(id=source, status=status,
                                    unavailable_reason=code if code in SAFE_CODES else "SOURCE_ERROR")
            status = "limited" if search.coverage.limited else search.status
            return SourceResult(id=source, status=status, observed_at=search.queried_at, search=search)
        return adapter

    def search(self, request, store):
        with self._lock:
            operation = self._operation(request.operation_id)
            if operation.started:
                raise SessionError("PARALLEL_SEARCH_ALREADY_STARTED")
            operation.started = True
        query = request.query
        pending = {source: PendingSession(store) for source in self.services}
        adapters = {source: self._quote_adapter(source, stage) for source, stage in pending.items()}
        adapters["flyai"] = self.flyai if query.place_type == "城市" else (
            lambda *_: SourceResult(id="flyai", status="unavailable",
                                    unavailable_reason="UNSUPPORTED_PLACE_TYPE")
        )
        if not operation.cancelled.is_set():
            outcome = MultiSourceOrchestrator(adapters, deadline_seconds=self.deadline_seconds).run(
                MultiSourceRequest(query=query, sources=list(SOURCE_IDS), revision=0,
                                   max_http_per_source=QUOTE_BUDGET),
                cancelled=operation.cancelled, commit=False,
            )
        with self._lock:
            if operation.cancelled.is_set():
                return ParallelSearchResponse(
                    operation_id=request.operation_id, context=query, status="cancelled",
                    sources=[ParallelSourceResult.from_result(SourceResult(
                        id=source, status="cancelled", unavailable_reason="CANCELLED"))
                             for source in SOURCE_IDS],
                )
            sources = []
            for source in outcome.sources:
                if source.search is not None:
                    try:
                        pending[source.id].publish(source.search, store, self.services[source.id])
                    except (SessionError, sqlite3.Error, KeyError):
                        source = SourceResult(id=source.id, status="unavailable",
                                              unavailable_reason="CLIENT_ERROR", http_used=source.http_used)
                sources.append(source)
            operation.request_ids = tuple(source.search.request_id for source in sources if source.search)
            operation.query = query
            operation.discovery_hotels = tuple(
                (hotel.name, hotel.address)
                for source in sources if source.id == "flyai"
                for hotel in source.candidates
            )
        return ParallelSearchResponse(
            operation_id=request.operation_id, context=query,
            sources=[ParallelSourceResult.from_result(source) for source in sources],
            status="complete" if all(source.status == "complete" for source in sources) else "partial",
        )
