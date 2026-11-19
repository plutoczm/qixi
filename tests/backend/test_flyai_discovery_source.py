"""FlyAI is wired as a discovery source only: no money, room, stock or link."""
import threading

import pytest

from stay_agent.contracts import SearchInput
from stay_agent.flyai_source import (
    ALWAYS_UNVERIFIED, SOURCE_ID, describe_hint, make_flyai_adapter,
)
from stay_agent.multi_source import (
    MultiSourceOrchestrator, MultiSourceRequest, SourceContext,
)
from stay_agent.providers.data.flyai import FlyAIDiscoveryProvider
from stay_agent.providers.data.tuniu import TuniuFailure

OBSERVED = "2026-11-05T11:00:00+00:00"


def row(**extra):
    """Shape copied from the r51k live sample (values are synthetic)."""
    base = {
        "shId": "82213056",
        "name": "某民宿",
        "address": "广州-越秀区-某路1号",
        "price": "¥144",
        "latitude": 23.133191,
        "longitude": 113.285472,
        "star": "经济型",
        "interestsPoi": "近某地铁站",
        "detailUrl": "https://router.example.invalid/multi/webview?url=x",
        "mainPic": "https://img.example.invalid/a.jpg",
    }
    base.update(extra)
    return base


def payload(rows, *, status=0):
    return {"status": status, "message": "success", "data": {"itemList": rows}}


def query(**extra):
    base = {
        "place": "广州", "place_type": "城市", "check_in": "2026-11-13",
        "check_out": "2026-11-14", "adults": 2, "rooms": 1,
        "children_ages": [], "budget_fen": 20000, "limit": 20,
    }
    base.update(extra)
    return SearchInput(**base)


def provider(rows, *, mode="offline_test", calls=None):
    def call(args):
        if calls is not None:
            calls.append(args)
        return payload(rows) if not isinstance(rows, Exception) else rows
    return FlyAIDiscoveryProvider(call, mode=mode)


def context(*, max_http=8, cancel=False, deadline_offset=60.0):
    token = threading.Event()
    if cancel:
        token.set()
    now = [1000.0]
    return SourceContext(
        source_id=SOURCE_ID, max_http=max_http, deadline=now[0] + deadline_offset,
        clock=lambda: now[0], cancelled=token,
    )


# --- projection -------------------------------------------------------------

def test_hint_keeps_the_fields_the_real_sample_has():
    calls = []
    evidence = provider([row()], calls=calls).search(query(), city="广州")
    hint = evidence.hints[0]
    assert evidence.data_mode == "test"
    assert hint.opaque_id == "82213056"
    assert hint.price_text == "¥144"
    # No currency field exists, so the amount stays unknown.
    assert hint.search_price_fen is None
    assert round(hint.latitude, 4) == 23.1332
    assert round(hint.longitude, 4) == 113.2855
    assert hint.star == "经济型"
    assert hint.nearby_transit == "近某地铁站"
    assert hint.detail_url_present is True
    assert len(calls) == 1


@pytest.mark.parametrize(
    "lat, lon", [("23.1", 113.2), (0, 0), (91, 113.2), (23.1, 181)],
)
def test_implausible_coordinates_are_dropped_not_repaired(lat, lon):
    hint = provider([row(latitude=lat, longitude=lon)]).search(
        query(), city="广州",
    ).hints[0]
    assert hint.latitude is None or hint.longitude is None


def test_hint_view_never_exposes_the_detail_url():
    evidence = provider([row()]).search(query(), city="广州")
    view = describe_hint(evidence.hints[0])
    assert view["detail_url_present"] is True
    assert "router.example.invalid" not in str(view)


# --- adapter ----------------------------------------------------------------

def test_adapter_returns_candidates_with_every_unknown_flagged():
    adapter = make_flyai_adapter(provider([row()]))
    result = adapter(query(), context())
    assert result.id == SOURCE_ID and result.status == "complete"
    assert result.http_used == 1
    candidate = result.candidates[0]
    assert candidate.currency == "UNSTATED"
    assert candidate.reference_price_fen is None
    assert candidate.price_scope == "unknown"
    assert candidate.identity_status == "unverified"
    assert candidate.booking_status == "unverified"
    assert candidate.occupancy_status == "unverified"
    assert candidate.address == "广州-越秀区-某路1号"
    assert set(ALWAYS_UNVERIFIED) <= set(candidate.unverified)


def test_one_call_only_and_no_retry():
    calls = []
    make_flyai_adapter(provider([row()], calls=calls))(query(), context())
    assert len(calls) == 1


def test_budget_refusal_never_calls_the_source():
    calls = []
    result = make_flyai_adapter(provider([row()], calls=calls))(
        query(), context(max_http=0),
    )
    assert result.status == "unavailable"
    assert result.unavailable_reason == "BUDGET_EXHAUSTED"
    assert calls == []


def test_cancellation_is_reported_as_cancelled():
    calls = []
    result = make_flyai_adapter(provider([row()], calls=calls))(
        query(), context(cancel=True),
    )
    assert result.unavailable_reason == "CANCELLED"
    assert calls == []


def test_non_city_queries_are_refused_without_a_call():
    calls = []
    result = make_flyai_adapter(provider([row()], calls=calls))(
        query(place="某酒店", place_type="酒店"), context(),
    )
    assert result.status == "unavailable"
    assert result.unavailable_reason == "INVALID_DATA"
    assert calls == []


def test_disabled_provider_reports_unconfigured():
    adapter = make_flyai_adapter(FlyAIDiscoveryProvider(mode="disabled"))
    result = adapter(query(), context())
    assert result.status == "unconfigured"
    assert result.unavailable_reason == "CONFIGURATION_MISSING"


def test_cli_mode_without_the_bundle_is_unconfigured(monkeypatch, tmp_path):
    from stay_agent.providers.data import flyai_client

    # No bundle installed is a configuration gap, never a supplier failure.
    monkeypatch.setattr(flyai_client, "CLI_PATH", tmp_path / "missing.cjs")
    adapter = make_flyai_adapter(FlyAIDiscoveryProvider(mode="cli"))
    result = adapter(query(), context())
    assert result.status == "unconfigured"
    assert result.unavailable_reason == "CONFIGURATION_MISSING"
    assert result.candidates == []


def test_source_failure_is_mapped_to_a_safe_code():
    adapter = make_flyai_adapter(provider(TuniuFailure("SOURCE_ERROR")))
    result = adapter(query(), context())
    assert result.status == "unavailable"
    assert result.unavailable_reason == "SOURCE_ERROR"


def test_partial_hint_page_marks_the_result_partial():
    rows = [row(shId=str(8000 + i), name=f"某酒店{i}",
                address=f"广州-某区-某路{i}号") for i in range(25)]
    result = make_flyai_adapter(provider(rows))(query(), context())
    assert result.status == "partial"


# --- orchestration ----------------------------------------------------------

def test_orchestrator_keeps_sources_separate_and_never_merges():
    from stay_agent.multi_source import SourceCandidate, SourceResult

    def tuniu_adapter(_query, ctx):
        ctx.spend()
        return SourceResult(
            id="tuniu", status="complete", http_used=1, observed_at=OBSERVED,
            candidates=[SourceCandidate(
                source_id="tuniu", source_hotel_id="57316571",
                name="某民宿", address="广州-越秀区-某路1号", currency="CNY",
                price_scope="first_night_reference", observed_at=OBSERVED,
                reference_price_fen=14000,
            )],
        )

    orchestrator = MultiSourceOrchestrator(
        {"flyai": make_flyai_adapter(provider([row()])), "tuniu": tuniu_adapter},
    )
    result = orchestrator.run(MultiSourceRequest(
        query=query(), sources=["flyai", "tuniu"], revision=1, max_http_per_source=2,
    ))
    assert result.merged is None
    by_id = {item.id: item for item in result.sources}
    # Same name+address on both sides is still TWO candidates, never merged.
    assert len(by_id["flyai"].candidates) == 1
    assert len(by_id["tuniu"].candidates) == 1
    assert by_id["flyai"].candidates[0].currency == "UNSTATED"
    assert by_id["tuniu"].candidates[0].currency == "CNY"


@pytest.fixture(autouse=True)
def historical_fixture_validation_date(monkeypatch):
    """Replay fixed November fixtures without weakening real-run preflights."""
    from datetime import date
    monkeypatch.setattr('stay_agent.contracts.china_today',lambda:date(2026,11,10))
