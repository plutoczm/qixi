"""Source-isolated multi-channel candidate browsing (offline contract, Phase 1a).

This module makes NO supplier calls. Adapters are injected and must be
cooperative: they check ``SourceContext.spend()`` before each would-be HTTP call.
Every source gets its own HTTP budget and shares one deadline plus one cancel
token. Results are grouped by source; ``merged`` is ALWAYS None.

Invariants (see docs/spec.md):
  * success, failure, timeout, unconfigured and cancelled sources are all kept
    in the response -- a failed source is never silently dropped or backfilled;
  * no cross-source merge, deduplication, sorting or price ranking exists here;
  * no coordinates, no cross-source IDs, no promotion to "bookable";
  * unknown/qualified statuses stay unverified; only safe error codes are kept.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from dataclasses import dataclass
import threading
import time
from typing import Callable, Literal, Mapping, Protocol

from pydantic import Field, StrictInt, field_validator

from .contracts import Model, SearchInput, SearchResponse

SourceStatus = Literal[
    "complete", "partial", "limited", "unavailable",
    "unconfigured", "cancelled", "expired",
]

SAFE_CODES = frozenset((
    "TIMEOUT", "RATE_LIMITED", "AUTHENTICATION_FAILED", "ACCESS_DENIED",
    "CONFIGURATION_MISSING", "INVALID_RESPONSE", "INVALID_DATA", "BUSY",
    "TRANSPORT_ERROR", "SOURCE_ERROR", "CLIENT_ERROR", "DEADLINE_EXCEEDED",
    "CANCELLED", "UNREGISTERED_SOURCE", "SOURCE_ID_MISMATCH", "BUDGET_EXHAUSTED",
    "UNSUPPORTED_PLACE_TYPE", "UNSUPPORTED_REGION", "REQUEST_LIMIT",
))


class SourceFailure(Exception):
    """Adapter failure carrying only a safe code, never supplier text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SourceCandidate(Model):
    source_id: str
    source_hotel_id: str
    name: str
    #: Supplier address as displayed. Discovery sources need it for the exact
    #: name+address identity gate; it is never used across sources.
    address: str | None = None
    currency: str
    price_scope: Literal["first_night_reference", "supplier_display_quote", "unknown"]
    observed_at: str
    identity_status: Literal["matched", "unverified", "ambiguous"] = "unverified"
    booking_status: Literal["matched", "mismatch", "unverified", "unavailable"] = "unverified"
    occupancy_status: Literal["matched", "mismatch", "unverified"] = "unverified"
    unverified: list[str] = Field(default_factory=list)
    reference_price_fen: StrictInt | None = Field(default=None, ge=0, le=100000000)
    #: Verbatim supplier display string (e.g. "¥144"). Never parsed into a
    #: money amount here: currency and scope are unstated for discovery sources.
    price_text: str | None = Field(default=None, max_length=40)
    star: str | None = Field(default=None, max_length=40)
    nearby_transit: str | None = Field(default=None, max_length=80)
    #: Coordinates stay inside the source; only their presence is exported so
    #: no consumer can build a cross-source distance or route from them.
    coordinates_present: bool = False


class SourceResult(Model):
    id: str
    status: SourceStatus
    http_used: StrictInt = Field(default=0, ge=0)
    observed_at: str | None = None
    unavailable_reason: str | None = None
    candidates: list[SourceCandidate] = Field(default_factory=list)
    search: SearchResponse | None = None

    @field_validator("unavailable_reason")
    @classmethod
    def safe_reason(cls, value: str | None) -> str | None:
        if value is not None and value not in SAFE_CODES:
            raise ValueError("unsafe reason")
        return value


class MultiSourceRequest(Model):
    query: SearchInput
    sources: list[str] = Field(min_length=1, max_length=8)
    revision: StrictInt = Field(ge=0)
    max_http_per_source: StrictInt = Field(default=8, ge=1, le=32)

    @field_validator("sources")
    @classmethod
    def explicit_and_unique(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 40 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in item):
                raise ValueError("invalid source id")
            if item in cleaned:
                raise ValueError("duplicate source")
            cleaned.append(item)
        return cleaned


class MultiSourceResult(Model):
    revision: StrictInt
    sources: list[SourceResult]
    merged: Literal[None] = None
    warnings: list[str] = Field(default_factory=list)


@dataclass
class SourceContext:
    """Per-source cooperative budget. Never performs I/O by itself."""

    source_id: str
    max_http: int
    deadline: float
    clock: Callable[[], float]
    cancelled: threading.Event
    http_used: int = 0

    def spend(self) -> bool:
        """Count one would-be HTTP call, or refuse without incrementing."""
        if self.cancelled.is_set() or self.clock() >= self.deadline:
            return False
        if self.http_used >= self.max_http:
            return False
        self.http_used += 1
        return True


class SourceAdapter(Protocol):
    def __call__(self, query: SearchInput, ctx: SourceContext) -> SourceResult: ...


class MultiSourceOrchestrator:
    """Run explicitly selected sources in isolation and keep every outcome."""

    def __init__(
        self,
        adapters: Mapping[str, SourceAdapter],
        *,
        clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float = 60.0,
        max_workers: int = 4,
    ):
        if not adapters:
            raise ValueError("at least one adapter is required")
        if deadline_seconds <= 0 or max_workers < 1:
            raise ValueError("invalid orchestrator bounds")
        self.adapters = dict(adapters)
        self.clock = clock
        self.deadline_seconds = float(deadline_seconds)
        self.max_workers = max_workers
        self.committed: dict[int, MultiSourceResult] = {}
        self._latest_revision = -1
        self._lock = threading.Lock()

    def _run_one(self, source_id: str, query: SearchInput, ctx: SourceContext) -> SourceResult:
        adapter = self.adapters.get(source_id)
        if adapter is None:
            return SourceResult(
                id=source_id, status="unconfigured", http_used=ctx.http_used,
                unavailable_reason="UNREGISTERED_SOURCE",
            )
        try:
            result = adapter(query, ctx)
            if ctx.clock() >= ctx.deadline:
                return SourceResult(id=source_id, status="expired", http_used=ctx.http_used,
                                    unavailable_reason="DEADLINE_EXCEEDED")
            if not isinstance(result, SourceResult):
                return SourceResult(
                    id=source_id, status="unavailable", http_used=ctx.http_used,
                    unavailable_reason="INVALID_RESPONSE",
                )
            if result.id != source_id:
                return SourceResult(
                    id=source_id, status="unavailable", http_used=ctx.http_used,
                    unavailable_reason="SOURCE_ID_MISMATCH",
                )
            return result.model_copy(update={"http_used": ctx.http_used})
        except Exception as exc:  # noqa: BLE001 - failures must be explicit, never leaked
            code = getattr(exc, "code", None)
            safe = code if isinstance(code, str) and code in SAFE_CODES else "CLIENT_ERROR"
            return SourceResult(
                id=source_id, status="unavailable", http_used=ctx.http_used,
                unavailable_reason=safe,
            )

    def run(
        self,
        request: MultiSourceRequest,
        *,
        cancelled: threading.Event | None = None,
        commit: bool = True,
    ) -> MultiSourceResult:
        cancel_token = cancelled if cancelled is not None else threading.Event()
        deadline = self.clock() + self.deadline_seconds
        contexts: dict[str, SourceContext] = {}
        futures: dict[str, Future] = {}
        pool = ThreadPoolExecutor(max_workers=min(self.max_workers, len(request.sources)))
        try:
            for source_id in request.sources:
                ctx = SourceContext(
                    source_id=source_id,
                    max_http=request.max_http_per_source,
                    deadline=deadline,
                    clock=self.clock,
                    cancelled=cancel_token,
                )
                contexts[source_id] = ctx
                futures[source_id] = pool.submit(self._run_one, source_id, request.query, ctx)

            finished: set[Future] = set()
            remaining = max(0.0, deadline - self.clock())
            try:
                for future in as_completed(list(futures.values()), timeout=remaining):
                    finished.add(future)
            except FuturesTimeout:
                pass

            results: list[SourceResult] = []
            for source_id, future in futures.items():
                ctx = contexts[source_id]
                if future in finished:
                    try:
                        result = future.result()
                    except Exception:  # noqa: BLE001
                        result = SourceResult(
                            id=source_id, status="unavailable", http_used=ctx.http_used,
                            unavailable_reason="CLIENT_ERROR",
                        )
                else:
                    future.cancel()
                    result = SourceResult(
                        id=source_id, status="expired", http_used=ctx.http_used,
                        unavailable_reason="DEADLINE_EXCEEDED",
                    )
                if cancel_token.is_set() and result.status in ("complete", "partial", "limited"):
                    result = result.model_copy(
                        update={"status": "cancelled", "unavailable_reason": "CANCELLED",
                                "candidates": [], "search": None}
                    )
                results.append(result)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        response = MultiSourceResult(
            revision=request.revision,
            sources=results,
            merged=None,
            warnings=[
                "结果按来源分组，未做跨源合并、去重或最低价排序。",
                "失败、超时、未配置与取消的来源均显式保留；参考价不等于含费总价或可订价。",
            ],
        )
        if commit:
            with self._lock:
                stale = request.revision < self._latest_revision
                if not stale and not cancel_token.is_set() and self.clock() < deadline:
                    self._latest_revision = request.revision
                    self.committed[request.revision] = response
        return response
