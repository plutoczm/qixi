"""Production map-first recall with synthetic map/quote adapters; no provider requests."""

from types import SimpleNamespace
from uuid import uuid4
import threading

import pytest
from pydantic import ValidationError
from stay_agent.providers.data.rollinggo_client import RollingGoClient, RollingGoError
from stay_agent.providers.data.travel_clients import ProbeError
from stay_agent.contracts import RecallInput, BranchWalkBatchRequest
from stay_agent.recall import RecallService
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore
from stay_agent.branch_walk import BranchWalkService
from test_recall import Maps, Quotes, points, hotel, request
from test_branch_walk import Map as WalkMap


class NearbyMaps(Maps):
    def __init__(self, count=3, neighborhoods=None):
        super().__init__([points(1, count)])
        self.neighborhoods = neighborhoods
        self.around_calls = []
        self.http = SimpleNamespace(request_count=0)
        self.on_around = None

    def search(self, *args, **kwargs):
        self.http.request_count += 1
        return super().search(*args, **kwargs)

    def around(self, point, keyword, limit):
        self.http.request_count += 1
        self.around_calls.append((point.poi_id, keyword, limit))
        if self.on_around:
            self.on_around()
        rows = (
            self.neighborhoods[int(point.poi_id)]
            if self.neighborhoods is not None
            else [
                SimpleNamespace(
                    poi_id=f"H{int(point.poi_id) * 10 + i}",
                    title=f"测试酒店{int(point.poi_id) * 10 + i}",
                )
                for i in range(3)
            ]
        )
        if isinstance(rows, Exception):
            raise rows
        for p in rows:
            ident = p.poi_id.removeprefix("H")
            self.metadata.setdefault(p.poi_id, {"address": f"测试街{ident}号", "city": "广州市"})
        return rows


class Store(SessionStore):
    def __init__(self, path):
        super().__init__(path)
        self.saves = 0

    def save(self, *args, **kwargs):
        self.saves += 1
        return super().save(*args, **kwargs)


def setup(tmp_path, count=3, neighborhoods=None, outputs=None, clock=None):
    maps = NearbyMaps(count, neighborhoods)
    source = Quotes(outputs if outputs is not None else [[hotel(i)] for i in [10, 20, 30, 11, 21]])
    svc = RecallService(
        quote_service=QuoteService(),
        map_factory=lambda provider, deadline: maps,
        quote_factory=lambda deadline: source,
        clock=clock or __import__("time").monotonic,
        data_mode="test",
    )
    store = Store(tmp_path / "map-first.sqlite3")
    return svc, maps, source, store


@pytest.mark.parametrize("provider", ["amap", "tencent"])
def test_round_robin_global_five_and_original_context(tmp_path, provider):
    svc, maps, source, store = setup(tmp_path)
    result = svc.search(request(strategy="map_hotels", provider=provider), store)
    assert [a["place"] for a in source.arguments] == [
        "测试酒店10",
        "测试酒店20",
        "测试酒店30",
        "测试酒店11",
        "测试酒店21",
    ]
    assert len(maps.around_calls) == 3 and all(c[2] == 10 for c in maps.around_calls)
    assert result.strategy == "map_hotels" and result.recall_coverage.hotel_lookups_attempted == 5
    assert result.recall_coverage.unqueried_targets == 4
    assert result.status == "partial" and len(result.hotels) == 5 and store.saves == 1
    for a in source.arguments:
        assert a["placeType"] == "酒店" and a["hotelTags"]["maxPricePerNight"] == 200
        assert a["checkInParam"]["adultCount"] == 2 and a["checkInParam"]["stayNights"] == 2
    assert store.load(result.request_id, 10)["children_ages"] == [5]
    assert store.load(result.request_id, 10)["rooms"] == 2
    assert result.walk_check_available
    assert [len(b.hotel_ids) for b in result.branches] == [2, 2, 1]


def test_wrong_branch_is_excluded_and_not_saved_as_member(tmp_path):
    target = SimpleNamespace(poi_id="H10", title="测试酒店10")
    svc, _, _, store = setup(tmp_path, 1, {1: [target]}, [[hotel(900, name="其他分店")]])
    result = svc.search(request(strategy="map_hotels"), store)
    assert result.hotels == [] and result.status == "complete"
    assert result.branches[0].hotel_targets[0].status == "excluded"
    assert result.recall_coverage.excluded_source_hotels == 1 and not result.walk_check_available
    assert store.saves == 1
    with pytest.raises(Exception):
        store.load(result.request_id, 900)


def test_same_name_address_difference_remains_unverified_with_anchor(tmp_path):
    target = SimpleNamespace(poi_id="H10", title="测试酒店10")
    svc, _, _, store = setup(tmp_path, 1, {1: [target]}, [[hotel(10, address="测试街10号1楼")]])
    result = svc.search(request(strategy="map_hotels"), store)
    chosen = result.branches[0].hotel_targets[0]
    assert chosen.status == "retained" and chosen.match_status == "unverified"
    ref = svc.association(result.request_id)["branches"][0]["hotel_targets"][10]
    assert ref["source_id"] == "H10" and ref["address"] == "测试街10号"


def test_multiple_exact_source_ids_are_ambiguous_not_first_selected(tmp_path):
    target = SimpleNamespace(poi_id="H10", title="测试酒店10")
    rows = [hotel(10), hotel(99, name="测试酒店10", address="测试街10号")]
    svc, _, _, store = setup(tmp_path, 1, {1: [target]}, [rows])
    result = svc.search(request(strategy="map_hotels"), store)
    assert not result.hotels and result.branches[0].hotel_targets[0].status == "ambiguous"


def test_same_id_conflicting_identity_even_in_rejected_rows_excludes_whole_id(tmp_path):
    svc, _, _, store = setup(
        tmp_path, outputs=[[hotel(10)], [hotel(10, name="冲突名"), hotel(20)], [hotel(30)], [], []]
    )
    result = svc.search(request(strategy="map_hotels"), store)
    assert 10 not in [h.id for h in result.hotels]
    assert result.recall_coverage.identity_conflicts == 1
    assert result.branches[0].hotel_targets[0].reason == "identity_conflict"


def test_repeated_same_map_hotel_is_queried_once_and_keeps_all_branches(tmp_path):
    target = SimpleNamespace(poi_id="H10", title="测试酒店10")
    svc, _, source, store = setup(
        tmp_path, neighborhoods={1: [target], 2: [target], 3: [target]}, outputs=[[hotel(10)]]
    )
    result = svc.search(request(strategy="map_hotels"), store)
    assert len(source.arguments) == 1 and result.recall_coverage.lookup_reuses == 2
    assert [b.hotel_ids for b in result.branches] == [[10], [10], [10]]
    assert len(result.hotels) == 1 and result.recall_coverage.duplicate_hotels == 2


def test_same_name_different_map_anchors_cannot_merge_one_supplier_identity(tmp_path):
    a = SimpleNamespace(poi_id="H10", title="测试酒店10")
    b = SimpleNamespace(poi_id="H11", title="测试酒店10")
    svc, maps, source, store = setup(tmp_path, 2, {1: [a], 2: [b]}, [[hotel(10)]])
    result = svc.search(request(strategy="map_hotels"), store)
    assert len(source.arguments) == 1 and not result.hotels
    assert result.recall_coverage.identity_conflicts == 1
    assert all(b.hotel_targets[0].reason == "identity_conflict" for b in result.branches)


def test_attached_or_invalid_places_are_not_used_for_quotes(tmp_path):
    names = ["测试酒店停车场", "测试酒店大堂", "测试酒店餐厅", "测试酒店健身房"]
    rows = [SimpleNamespace(poi_id=f"H{i}", title=name) for i, name in enumerate(names)]
    svc, _, source, store = setup(tmp_path, 1, {1: rows}, [])
    result = svc.search(request(strategy="map_hotels"), store)
    assert not source.arguments and not result.hotels and result.status == "complete"
    assert result.recall_coverage.nearby_invalid == 4


def test_neighborhood_failure_keeps_other_success_and_source_failure_is_not_empty(tmp_path):
    svc, _, _, store = setup(
        tmp_path,
        2,
        {1: ProbeError("TIMEOUT"), 2: [SimpleNamespace(poi_id="H20", title="测试酒店20")]},
        [[hotel(20)]],
    )
    result = svc.search(request(strategy="map_hotels"), store)
    assert result.status == "partial" and [h.id for h in result.hotels] == [20]
    assert result.branches[0].nearby_status == "unavailable"


def test_all_quote_failures_are_unavailable_not_empty_inventory(tmp_path):
    svc, _, _, store = setup(tmp_path, outputs=[RollingGoError("TIMEOUT")] * 5)
    result = svc.search(request(strategy="map_hotels"), store)
    assert result.status == "unavailable" and not result.hotels and store.saves == 0


def test_auth_failure_stops_further_name_calls(tmp_path):
    svc, _, source, store = setup(tmp_path, outputs=[RollingGoError("ACCESS_DENIED")])
    result = svc.search(request(strategy="map_hotels"), store)
    assert len(source.arguments) == 1 and result.status == "unavailable"
    assert (
        sum(t.reason == "quote_source_blocked" for b in result.branches for t in b.hotel_targets)
        == 8
    )


def test_pre_cancel_neighborhood_cancel_and_late_source_are_not_committed(tmp_path):
    for index, where in enumerate(["before", "map", "quote"]):
        folder = tmp_path / str(index)
        folder.mkdir()
        svc, maps, source, store = setup(folder)
        req = request(strategy="map_hotels")
        if where == "before":
            svc.cancel(req.operation_id)
        elif where == "map":
            maps.on_around = lambda: svc.cancel(req.operation_id)
        else:
            source.outputs = iter([lambda: (svc.cancel(req.operation_id), [hotel(10)])[1]])
        result = svc.search(req, store)
        assert result.status == "unavailable" and not result.hotels and store.saves == 0
        assert len(source.arguments) == (1 if where == "quote" else 0)
        if where == "before":
            assert not maps.calls


def test_deadline_rejects_late_query_and_stops_more_names(tmp_path):
    clock = [0.0]
    svc, _, source, store = setup(tmp_path, clock=lambda: clock[0])
    source.outputs = iter([lambda: (clock.__setitem__(0, 61), [hotel(10)])[1]])
    result = svc.search(request(strategy="map_hotels"), store)
    assert len(source.arguments) == 1 and not result.hotels and store.saves == 0


def test_invalid_strategy_and_long_branch_reference(tmp_path):
    with pytest.raises(ValidationError):
        RecallInput(**{**request().model_dump(), "strategy": "automatic_fallback"})
    svc, maps, _, store = setup(tmp_path, 1, outputs=[[hotel(10)], [hotel(11)], [hotel(12)]])
    original = maps.search

    def search(*args, **kwargs):
        rows = original(*args, **kwargs)
        maps.metadata["1"]["address"] = "完整地址" * 40
        return rows

    maps.search = search
    result = svc.search(request(strategy="map_hotels"), store)
    assert result.branches[0].nearby_status == "complete"


@pytest.mark.parametrize("changed", [False, True])
def test_walk_preserves_map_anchor_and_requires_review_if_anchor_id_changes(tmp_path, changed):
    target = SimpleNamespace(poi_id="H10", title="测试酒店10")
    svc, _, _, store = setup(tmp_path, 1, {1: [target]}, [[hotel(10)]])
    result = svc.search(request(strategy="map_hotels"), store)

    class FreshMap(WalkMap):
        def search(self, *args, **kwargs):
            rows = super().search(*args, **kwargs)
            if changed and "酒店" in args[0]:
                old = rows[0]
                new = SimpleNamespace(poi_id="new-H10", title=old.title)
                self.metadata[new.poi_id] = self.metadata[old.poi_id]
                return [new]
            return rows

    maps = FreshMap()
    walk = BranchWalkService(svc, store, map_factory=lambda *args: maps, data_mode="test")
    pair = walk.batch(
        BranchWalkBatchRequest(request_id=result.request_id, operation_id=uuid4().hex)
    ).pairs[0]
    assert pair.recall_hotel.name == "测试酒店10"
    # RollingGo did not independently supply a city: matching map POI names,
    # addresses and anchors must NOT silently authorize an automatic walk.
    assert pair.walk is None
    assert pair.hotel_matching.status == "unverified"
    if changed:
        assert "地图酒店参考不一致" in pair.hotel_matching.explanation
    else:
        assert "道旅未独立回显酒店城市" in pair.hotel_matching.explanation


def test_native_quote_cancellation_stops_before_transport():
    def forbidden(*args):
        raise AssertionError("No network allowed after pre-cancel")

    client = RollingGoClient("synthetic-test-key", transport=forbidden)
    client.cancelled = threading.Event()
    client.cancelled.set()
    with pytest.raises(RollingGoError, match="CANCELLED"):
        client.call_tool("searchHotels", {"place": "测试"})
    assert client.request_count == 0


def test_map_first_api_returns_bound_strategy_and_no_store(tmp_path):
    from fastapi.testclient import TestClient
    from stay_agent.main import create_app

    svc, _, source, store = setup(tmp_path)
    conv = SimpleNamespace(recover=lambda: None, close=lambda: None)
    app = create_app(
        service=svc.quotes,
        session_store=store,
        recall_service=svc,
        conversation_service=conv,
        web_root=tmp_path / "none",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = request(strategy="map_hotels").model_dump(mode="json")
        response = client.post("/api/recall", json=payload)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["strategy"] == "map_hotels"
        assert data["recall_coverage"]["hotel_lookups_attempted"] == 5
        assert len(source.arguments) == 5
        assert (
            client.post("/api/recall", json={**payload, "strategy": "unknown"}).status_code == 422
        )


def test_five_branch_option_shares_one_five_name_budget(tmp_path):
    svc, maps, source, store = setup(
        tmp_path, count=5, outputs=[[hotel(i)] for i in [10, 20, 30, 40, 50]]
    )
    result = svc.search(request(strategy="map_hotels", branch_limit=5), store)
    assert len(source.arguments) == 5 and len(maps.around_calls) == 5
    assert [a["place"] for a in source.arguments] == [f"测试酒店{i}" for i in [10, 20, 30, 40, 50]]
    assert result.recall_coverage.hotel_lookups_attempted == 5
    assert result.recall_coverage.unqueried_targets == 10
    assert all(len(b.hotel_ids) == 1 for b in result.branches)
