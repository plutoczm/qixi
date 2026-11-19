"""Offline contract tests for source-isolated multi-channel browsing.

Zero supplier requests: every adapter is a synthetic callable. These tests pin
the six failure/identity shapes from
docs/spec.md.
"""
from __future__ import annotations

from datetime import timedelta
import threading

import pytest
from pydantic import ValidationError

from stay_agent.contracts import SearchInput, china_today
from stay_agent.multi_source import (
    MultiSourceOrchestrator,
    MultiSourceRequest,
    MultiSourceResult,
    SourceCandidate,
    SourceFailure,
    SourceResult,
)

OBSERVED = "2026-11-04T00:00:00+00:00"


def make_query() -> SearchInput:
    start = china_today() + timedelta(days=10)
    return SearchInput(
        place="广州", place_type="城市", check_in=start,
        check_out=start + timedelta(days=1), adults=2, rooms=1,
        budget_fen=20000, limit=20,
    )


def request(sources=("rollinggo", "tuniu"), revision=1, max_http=8) -> MultiSourceRequest:
    return MultiSourceRequest(
        query=make_query(), sources=list(sources),
        revision=revision, max_http_per_source=max_http,
    )


def candidate(source_id, hotel_id, name="广州市方舟公寓", currency="CNY",
              scope="first_night_reference", price_fen=11200) -> SourceCandidate:
    return SourceCandidate(
        source_id=source_id, source_hotel_id=hotel_id, name=name,
        currency=currency, price_scope=scope, observed_at=OBSERVED,
        reference_price_fen=price_fen,
        unverified=["独卫", "足量库存", "含费总价", "逐夜总价"],
    )


def ok(result=None, *, calls=None, spends=1):
    def adapter(query, ctx):
        if calls is not None:
            calls.append(ctx.source_id)
        for _ in range(spends):
            ctx.spend()
        return result or SourceResult(
            id=ctx.source_id, status="complete",
            observed_at=OBSERVED,
            candidates=[candidate(ctx.source_id, f"{ctx.source_id}-1")],
        )
    return adapter


def by_id(response):
    return {item.id: item for item in response.sources}


def test_one_success_one_timeout_both_reported():
    """A failed source must stay visible; success is never used to backfill it."""
    good = SourceResult(
        id="rollinggo", status="complete", observed_at=OBSERVED,
        candidates=[candidate("rollinggo", "533535")],
    )

    def timed_out(query, ctx):
        return SourceResult(id="tuniu", status="unavailable", unavailable_reason="TIMEOUT")

    orch = MultiSourceOrchestrator({"rollinggo": ok(good), "tuniu": timed_out})
    response = orch.run(request())
    assert len(response.sources) == 2
    states = by_id(response)
    assert states["rollinggo"].status == "complete"
    assert states["tuniu"].status == "unavailable"
    assert states["tuniu"].unavailable_reason == "TIMEOUT"
    assert len(states["rollinggo"].candidates) == 1
    assert response.merged is None
    assert 1 in orch.committed


def test_same_name_different_ids_are_not_merged():
    first = SourceResult(
        id="rollinggo", status="complete", observed_at=OBSERVED,
        candidates=[candidate("rollinggo", "533535", name="广州市方舟公寓")],
    )
    second = SourceResult(
        id="tuniu", status="complete", observed_at=OBSERVED,
        candidates=[candidate("tuniu", "T-999", name="广州市方舟公寓")],
    )
    orch = MultiSourceOrchestrator({"rollinggo": ok(first), "tuniu": ok(second)})
    response = orch.run(request())
    assert response.merged is None
    assert sum(len(item.candidates) for item in response.sources) == 2
    ids = {item.candidates[0].source_hotel_id for item in response.sources}
    assert ids == {"533535", "T-999"}


def test_different_currency_and_price_scope_never_produce_unified_ranking():
    a = SourceResult(
        id="rollinggo", status="complete", observed_at=OBSERVED,
        candidates=[candidate("rollinggo", "1", currency="CNY", scope="first_night_reference")],
    )
    b = SourceResult(
        id="tuniu", status="complete", observed_at=OBSERVED,
        candidates=[candidate("tuniu", "2", currency="USD", scope="supplier_display_quote")],
    )
    orch = MultiSourceOrchestrator({"rollinggo": ok(a), "tuniu": ok(b)})
    response = orch.run(request())
    payload = response.model_dump()
    assert payload["merged"] is None
    assert not any(key in payload for key in ("lowest_price", "ranking", "sorted_candidates"))
    states = by_id(response)
    assert states["rollinggo"].candidates[0].currency == "CNY"
    assert states["tuniu"].candidates[0].currency == "USD"
    assert states["tuniu"].candidates[0].price_scope == "supplier_display_quote"
    with pytest.raises(ValidationError):
        MultiSourceResult(revision=1, sources=[], sorted_candidates=[])


def test_rate_limited_source_does_not_retry_or_affect_other_source():
    calls = []

    def limited(query, ctx):
        calls.append(ctx.source_id)
        raise SourceFailure("RATE_LIMITED")

    orch = MultiSourceOrchestrator({"rollinggo": ok(calls=calls), "tuniu": limited})
    response = orch.run(request())
    states = by_id(response)
    assert calls.count("tuniu") == 1
    assert states["tuniu"].status == "unavailable"
    assert states["tuniu"].unavailable_reason == "RATE_LIMITED"
    assert states["rollinggo"].status == "complete"


def test_cancelled_and_stale_responses_are_not_committed():
    cancelled = threading.Event()
    cancelled.set()
    orch = MultiSourceOrchestrator({"rollinggo": ok(), "tuniu": ok()})
    response = orch.run(request(revision=1), cancelled=cancelled)
    assert 1 not in orch.committed
    assert all(item.status == "cancelled" for item in response.sources)

    orch2 = MultiSourceOrchestrator({"rollinggo": ok()})
    orch2.run(request(sources=("rollinggo",), revision=5))
    orch2.run(request(sources=("rollinggo",), revision=3))
    assert 5 in orch2.committed
    assert 3 not in orch2.committed


def test_budget_cap_stops_source_without_extra_calls():
    spent = []

    def greedy(query, ctx):
        while ctx.spend():
            spent.append(ctx.source_id)
        return SourceResult(
            id="rollinggo", status="limited",
            unavailable_reason="BUDGET_EXHAUSTED",
        )

    orch = MultiSourceOrchestrator({"rollinggo": greedy})
    response = orch.run(request(sources=("rollinggo",), max_http=2))
    assert len(spent) == 2
    assert response.sources[0].http_used == 2
    assert response.sources[0].status == "limited"
    assert response.sources[0].unavailable_reason == "BUDGET_EXHAUSTED"


def test_unregistered_source_is_explicit_unconfigured():
    orch = MultiSourceOrchestrator({"rollinggo": ok()})
    response = orch.run(request(sources=("rollinggo", "unknown")))
    states = by_id(response)
    assert states["unknown"].status == "unconfigured"
    assert states["unknown"].unavailable_reason == "UNREGISTERED_SOURCE"
    assert states["rollinggo"].status == "complete"


def test_source_cannot_impersonate_another_source():
    def impostor(query, ctx):
        return SourceResult(id="rollinggo", status="complete")

    orch = MultiSourceOrchestrator({"tuniu": impostor})
    response = orch.run(request(sources=("tuniu",)))
    assert response.sources[0].status == "unavailable"
    assert response.sources[0].unavailable_reason == "SOURCE_ID_MISMATCH"


def test_request_rejects_empty_duplicate_and_unsafe_source_ids():
    with pytest.raises(ValidationError):
        request(sources=())
    with pytest.raises(ValidationError):
        request(sources=("rollinggo", "rollinggo"))
    with pytest.raises(ValidationError):
        request(sources=("rolling go",))
    with pytest.raises(ValidationError):
        request(sources=("../../etc",))


def test_coordinates_and_unsafe_reasons_cannot_enter_contract():
    with pytest.raises(ValidationError):
        SourceCandidate(
            source_id="rollinggo", source_hotel_id="1", name="某酒店",
            currency="CNY", price_scope="unknown", observed_at=OBSERVED,
            latitude=23.1,
        )
    with pytest.raises(ValidationError):
        SourceResult(id="tuniu", status="unavailable", unavailable_reason="raw supplier text")
