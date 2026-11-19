"""Continuation exercises the production pipeline with offline source adapters."""

import copy
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from stay_agent.contracts import RecallContinueRequest
from stay_agent.recall_continuation import ContinuationError
from stay_agent.recall import RecallService, RecallError
from stay_agent.service import QuoteService
from stay_agent.storage import SessionError
from test_map_recall import NearbyMaps, Store
from test_recall import Quotes, hotel, request


ORDER = [10, 20, 30, 11, 21, 31, 12, 22, 32]


def setup(tmp_path, outputs=None, clock=None):
    maps = NearbyMaps()
    sequence = iter(outputs if outputs is not None else [[hotel(i)] for i in ORDER])
    clients = []

    def factory(deadline):
        client = Quotes(sequence)
        clients.append(client)
        return client

    svc = RecallService(
        quote_service=QuoteService(),
        map_factory=lambda *a: maps,
        quote_factory=factory,
        clock=clock or __import__("time").monotonic,
        data_mode="test",
    )
    store = Store(tmp_path / "continuation.sqlite3")
    first = svc.search(request(strategy="map_hotels"), store)
    return svc, store, first, clients, maps


def follow(first, operation=None):
    return RecallContinueRequest(
        request_id=first.request_id,
        continuation_cursor=first.continuation_cursor,
        operation_id=operation or uuid4().hex,
    )


def test_next_round_only_new_names_delta_and_no_new_maps(tmp_path):
    svc, store, first, clients, maps = setup(tmp_path)
    before = copy.deepcopy(first)
    next_page = svc.continuation.run(follow(first), store)
    assert [a["place"] for a in clients[1].arguments] == [f"测试酒店{i}" for i in ORDER[5:]]
    assert len(maps.around_calls) == 3 and len(maps.calls) == 1
    assert next_page.request_id == first.request_id and next_page.revision == 1
    assert next_page.round_number == 2 and next_page.continuation_delta
    assert next_page.continuation_cursor is None and next_page.retained_total == 9
    assert [h.id for h in next_page.hotels] == [12, 22, 31, 32]  # branch order
    assert next_page.recall_coverage.total_hotel_lookups == 9
    assert next_page.recall_coverage.hotel_lookups_attempted == 4
    assert next_page.recall_coverage.total_quote_http_requests == 9
    assert len(first.attempted_hotel_names) == 5
    assert len(next_page.attempted_hotel_names) == 9
    assert set(first.attempted_hotel_names) < set(next_page.attempted_hotel_names)
    assert first == before
    for ident in ORDER:
        assert store.load(first.request_id, ident)["rooms"] == 2
    assert svc.association(first.request_id)["hotels"].keys() == set(ORDER)


def test_state_keeps_only_metadata_not_quote_objects_or_amounts(tmp_path):
    svc, store, first, _, _ = setup(tmp_path)
    svc.continuation.run(follow(first), store)
    state = svc.continuation.states[first.request_id]

    def inspect(value):
        if isinstance(value, dict):
            assert not set(value) & {
                "reference_price_fen",
                "booking_url",
                "image_url",
                "lng",
                "lat",
                "currency",
                "walk",
            }
            for child in value.values():
                inspect(child)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                inspect(child)
        else:
            assert value.__class__.__name__ not in {
                "HotelSummary",
                "QuotesResponse",
                "MapProbe",
                "Point",
            }

    inspect(state.meta)
    inspect(state.progress)
    inspect(state.hotels)
    assert set(state.hotels[10]) == {"id", "name", "address"}


def test_stale_cursor_and_duplicate_operation_cannot_replay(tmp_path):
    svc, store, first, clients, _ = setup(tmp_path)
    req = follow(first)
    svc.continuation.run(req, store)
    with pytest.raises(RecallError, match="RECALL_OPERATION_CONFLICT"):
        svc.continuation.run(req, store)
    with pytest.raises(ContinuationError, match="CONTINUATION_STALE"):
        svc.continuation.run(follow(first), store)
    assert len(clients) == 2


def test_cursor_is_bound_to_request_and_query_is_not_client_editable(tmp_path):
    svc, store, first, clients, _ = setup(tmp_path)
    with pytest.raises(ContinuationError, match="CONTINUATION_NOT_FOUND"):
        svc.continuation.run(follow(first).model_copy(update={"request_id": uuid4().hex}), store)
    with pytest.raises(ValidationError):
        RecallContinueRequest(**{**follow(first).model_dump(), "query": {"budget_fen": None}})
    assert len(clients) == 1


def test_pre_cancel_makes_no_request_and_leaves_cursor_usable(tmp_path):
    svc, store, first, clients, _ = setup(tmp_path)
    req = follow(first)
    svc.cancel(req.operation_id)
    with pytest.raises(ContinuationError, match="CANCELLED"):
        svc.continuation.run(req, store)
    assert len(clients) == 1
    assert svc.continuation.run(follow(first), store).retained_total == 9


def test_inflight_cancel_reserves_sent_name_without_publishing_or_repeating_it(tmp_path):
    svc, store, first, clients, _ = setup(tmp_path)
    req = follow(first)
    sequence = iter(
        [
            lambda: (svc.cancel(req.operation_id), [hotel(31)])[1],
            [hotel(12)],
            [hotel(22)],
            [hotel(32)],
        ]
    )

    def factory(deadline):
        client = Quotes(sequence)
        clients.append(client)
        return client

    svc.quote_factory = factory
    with pytest.raises(ContinuationError, match="CANCELLED"):
        svc.continuation.run(req, store)
    with pytest.raises(SessionError, match="HOTEL_NOT_IN_SEARCH"):
        store.load(first.request_id, 31)
    assert svc.continuation.states[first.request_id].cursor == first.continuation_cursor
    final = svc.continuation.run(follow(first), store)
    assert [a["place"] for a in clients[1].arguments] == ["测试酒店31"]
    assert [a["place"] for a in clients[2].arguments] == ["测试酒店12", "测试酒店22", "测试酒店32"]
    assert final.retained_total == 8
    assert final.recall_coverage.total_hotel_lookups == 9
    assert any(t.reason == "attempt_interrupted" for b in final.branches for t in b.hotel_targets)


def test_same_queue_is_single_flight_without_sending_a_second_client(tmp_path):
    svc, store, first, clients, _ = setup(tmp_path)

    def nested():
        with pytest.raises(ContinuationError, match="CONTINUATION_BUSY"):
            svc.continuation.run(follow(first), store)
        return [hotel(31)]

    svc.quote_factory = lambda deadline: Quotes(
        iter([nested, [hotel(12)], [hotel(22)], [hotel(32)]])
    )
    assert svc.continuation.run(follow(first), store).retained_total == 9
    assert len(clients) == 1


def test_cross_round_identity_conflict_retracts_old_member_and_anchor(tmp_path):
    outputs = [[hotel(i)] for i in ORDER[:5]] + [
        [hotel(10, name="冲突住宿身份")],
        [hotel(12)],
        [hotel(22)],
        [hotel(32)],
    ]
    svc, store, first, _, _ = setup(tmp_path, outputs)
    result = svc.continuation.run(follow(first), store)
    assert result.removed_hotel_ids == [10] and result.retained_total == 7
    assert result.recall_coverage.identity_conflicts == 1
    assert 10 not in svc.association(first.request_id)["hotels"]
    with pytest.raises(SessionError, match="HOTEL_NOT_IN_SEARCH"):
        store.load(first.request_id, 10)
    assert result.branches[0].hotel_targets[0].reason == "identity_conflict"


def test_first_observation_time_does_not_refresh_when_source_repeats_id(tmp_path):
    outputs = [[hotel(i)] for i in ORDER[:5]] + [
        [hotel(31), hotel(10, price=999)],
        [hotel(12)],
        [hotel(22)],
        [hotel(32)],
    ]
    svc, store, first, _, _ = setup(tmp_path, outputs)
    result = svc.continuation.run(follow(first), store)
    assert 10 not in [h.id for h in result.hotels]
    before = next(o.queried_at for o in first.hotel_observations if o.hotel_id == 10)
    assert next(o.queried_at for o in result.hotel_observations if o.hotel_id == 10) == before


def test_absolute_sql_and_cursor_expiry_are_not_extended(tmp_path):
    svc, store, first, _, _ = setup(tmp_path)
    with sqlite3.connect(store.path) as db:
        before = db.execute(
            "SELECT expires_at FROM search_sessions WHERE request_id=?", (first.request_id,)
        ).fetchone()[0]
    state_expiry = svc.continuation.states[first.request_id].expires
    result = svc.continuation.run(follow(first), store)
    with sqlite3.connect(store.path) as db:
        after = db.execute(
            "SELECT expires_at FROM search_sessions WHERE request_id=?", (first.request_id,)
        ).fetchone()[0]
    assert before == after
    assert result.continuation_expires_at == first.continuation_expires_at
    assert svc.continuation.states[first.request_id].expires == state_expiry


def test_expired_state_or_expired_database_precedes_any_source_call(tmp_path):
    for n, database in enumerate([False, True]):
        folder = tmp_path / str(n)
        folder.mkdir()
        clock = [0.0]
        svc, store, first, clients, _ = setup(folder, clock=lambda: clock[0])
        if database:
            store.clock = lambda: __import__("time").time() + 1900
        else:
            clock[0] = 1900
        with pytest.raises(ContinuationError, match="CONTINUATION_EXPIRED"):
            svc.continuation.run(follow(first), store)
        assert len(clients) == 1


def test_restart_has_no_cursor_and_cannot_replay_state(tmp_path):
    svc, store, first, clients, _ = setup(tmp_path)
    svc.continuation.states.clear()
    with pytest.raises(ContinuationError, match="CONTINUATION_NOT_FOUND"):
        svc.continuation.run(follow(first), store)
    assert len(clients) == 1


def test_membership_replace_rolls_back_atomically(tmp_path):
    _, store, first, _, _ = setup(tmp_path)
    with pytest.raises(KeyError):
        store.replace_membership(first.request_id, [31], [{"id": 31}])
    assert store.hotel(first.request_id, 10)["name"] == "测试酒店10"
    with pytest.raises(SessionError):
        store.load(first.request_id, 31)


def test_sql_commit_failure_does_not_publish_cursor_or_repeat_sent_names(tmp_path, monkeypatch):
    svc, store, first, clients, _ = setup(tmp_path)
    original = store.replace_membership
    monkeypatch.setattr(
        store,
        "replace_membership",
        lambda *a: (_ for _ in ()).throw(SessionError("SEARCH_EXPIRED")),
    )
    with pytest.raises(ContinuationError, match="CONTINUATION_EXPIRED"):
        svc.continuation.run(follow(first), store)
    assert svc.continuation.states[first.request_id].cursor == first.continuation_cursor
    monkeypatch.setattr(store, "replace_membership", original)
    final = svc.continuation.run(follow(first), store)
    assert final.hotels == [] and final.retained_total == 5
    assert len(clients) == 2  # no further quote calls on names whose result was not published


def test_api_continuation_contract_and_no_store(tmp_path):
    from stay_agent.main import create_app

    svc, store, first, clients, _ = setup(tmp_path)
    conv = SimpleNamespace(recover=lambda: None, close=lambda: None)
    app = create_app(
        service=svc.quotes,
        session_store=store,
        recall_service=svc,
        conversation_service=conv,
        web_root=tmp_path / "none",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/recall/continue", json=follow(first).model_dump())
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["continuation_delta"] and data["retained_total"] == 9
        assert (
            client.post("/api/recall/continue", json=follow(first).model_dump()).status_code == 409
        )
        assert (
            client.post(
                "/api/recall/continue", json={**follow(first).model_dump(), "coordinates": [1, 2]}
            ).status_code
            == 422
        )
    assert len(clients) == 2


def test_source_auth_block_ends_continuation_but_keeps_previous_members(tmp_path):
    from stay_agent.providers.data.rollinggo_client import RollingGoError

    outputs = [[hotel(i)] for i in ORDER[:5]] + [RollingGoError("ACCESS_DENIED")]
    svc, store, first, clients, _ = setup(tmp_path, outputs)
    result = svc.continuation.run(follow(first), store)
    assert result.retained_total == 5 and result.hotels == []
    assert result.continuation_cursor is None and result.error.code == "ACCESS_DENIED"
    assert len(clients[1].arguments) == 1
    assert store.load(first.request_id, 10)["rooms"] == 2


def test_deadline_keeps_prior_results_and_marks_sent_unknown_name(tmp_path):
    clock = [0.0]
    outputs = [[hotel(i)] for i in ORDER[:5]] + [lambda: (clock.__setitem__(0, 61), [hotel(31)])[1]]
    svc, store, first, clients, _ = setup(tmp_path, outputs, clock=lambda: clock[0])
    result = svc.continuation.run(follow(first), store)
    assert result.error.code == "TIMEOUT"
    assert result.retained_total == 5 and result.hotels == []
    assert result.recall_coverage.hotel_lookups_attempted == 1
    assert len(clients[1].arguments) == 1
    assert result.continuation_cursor != first.continuation_cursor


def test_queue_capacity_is_bounded_and_expired_slots_are_reclaimed(tmp_path):
    svc, store, first, _, _ = setup(tmp_path)
    manager = svc.continuation
    state = manager.states[first.request_id]
    manager.states = {str(i): state for i in range(64)}
    response = first.model_copy(deep=True)
    response.continuation_cursor = None
    manager.remember(state.request, response, state.progress, store)
    assert len(manager.states) == 64 and response.continuation_cursor is None
    state.expires = svc.clock() - 1
    manager.remember(state.request, response, state.progress, store)
    assert len(manager.states) == 1 and response.continuation_cursor is not None
