"""Recall-bound identity and walk pipeline; all source data here is synthetic."""

import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stay_agent.branch_walk import BranchWalkService, BranchWalkError
from stay_agent.contracts import BranchWalkBatchRequest, BranchWalkRouteRequest
from stay_agent.main import create_app
from stay_agent.service import QuoteService
from stay_agent.providers.data.travel_clients import ProbeError, SecureHTTP
from test_recall import setup as recall_setup, points, hotel, request as recall_request


class Map:
    def __init__(self):
        self.metadata = {}
        self.calls = []
        self.walks = []
        self.drives = []
        self.drive_seconds = [840, 900, 901]
        self.drive_meters = [3200, 3300, 3500]
        self.empty_walk_indices = set()
        self.empty_drive_indices = set()
        self.on_drive = None
        self.hotel_mismatch = False
        self.restaurant_mismatch = False
        self.changed_restaurant_id = False
        self.seconds = [600, 601, 540]
        self.meters = [900, 700, 11000]
        self.http = SimpleNamespace(request_count=0, deadline=None)
        self.on_search = None
        self.on_walk = None

    def search(self, keyword, city, page_size):
        self.http.request_count += 1
        self.calls.append(keyword)
        if self.on_search:
            self.on_search()
        index = int(re.search(r"\d+", keyword)[0])
        if "酒店" in keyword:
            point = SimpleNamespace(poi_id="H" + str(index), title="测试酒店" + str(index))
            address = "测试街" + str(index) + "号" + ("1楼" if self.hotel_mismatch else "")
        else:
            point = SimpleNamespace(
                poi_id=("changed" if self.changed_restaurant_id else "") + str(index),
                title=f"测试餐厅{index}店" + ("新" if self.restaurant_mismatch else ""),
            )
            address = "测试路" + str(index) + "号"
        self.metadata[point.poi_id] = {"address": address, "city": "广州市"}
        return [point]

    def walk(self, origin, destination):
        self.http.request_count += 1
        self.walks.append((origin.title, destination.title))
        if self.on_walk:
            self.on_walk()
        index = len(self.walks) - 1
        return [] if index in self.empty_walk_indices else [
            {"distance_meters": self.meters[index % 3], "duration_seconds": self.seconds[index % 3]}
        ]

    def drive(self, origin, destination):
        self.http.request_count += 1
        self.drives.append((origin.title, destination.title))
        if self.on_drive:
            self.on_drive()
        index = len(self.drives) - 1
        return [] if index in self.empty_drive_indices else [
            {
                "distance_meters": self.drive_meters[index % len(self.drive_meters)],
                "duration_seconds": self.drive_seconds[index % len(self.drive_seconds)],
            }
        ]


def setup(tmp_path, shared_hotel=False, provider="amap"):
    rows = [[hotel(1)], [hotel(1 if shared_hotel else 2)], [hotel(1 if shared_hotel else 3)]]
    recall, _, _, sessions = recall_setup(tmp_path, [points(1, 3)], rows)
    result = recall.search(recall_request(provider=provider), sessions)
    client = Map()
    clock = [0.0]
    factories = []

    def factory(p, deadline, cancelled):
        factories.append(p)
        client.http.cancelled = cancelled
        return client

    svc = BranchWalkService(
        recall, sessions, map_factory=factory, clock=lambda: clock[0], data_mode="test"
    )
    return svc, result, client, clock, factories


def batch(result, **kwargs):
    return BranchWalkBatchRequest(request_id=result.request_id, operation_id=uuid4().hex, **kwargs)


def route_request(result, pair, **kwargs):
    return BranchWalkRouteRequest(
        request_id=result.request_id,
        context_id=pair.context_id,
        hotel_option_id=pair.hotels[0].id,
        restaurant_option_id=pair.restaurants[0].id,
        **kwargs,
    )


@pytest.mark.parametrize("provider", ["amap", "tencent"])
def test_strict_both_identities_direction_and_transport_or(tmp_path, provider):
    svc, result, client, _, factory = setup(tmp_path, provider=provider)
    response = svc.batch(batch(result))
    assert response.status == "complete" and response.processed_pairs == 3
    assert [p.walk_limit_status for p in response.pairs] == [
        "within_1500", "within_1500", "over_1500",
    ]
    assert [p.walk.distance_meters for p in response.pairs] == [900, 700, 11000]
    assert [p.transport_status for p in response.pairs] == ["pass_walk_or_drive"] * 3
    assert [p.drive_limit_status for p in response.pairs] == [
        "unverified", "unverified", "within_900",
    ]
    assert factory == [provider] and response.map_http_requests == 10
    assert client.drives == [("测试餐厅3店", "测试酒店3")]
    for index, (origin, destination) in enumerate(client.walks, 1):
        assert origin == f"测试餐厅{index}店" and destination == f"测试酒店{index}"
    assert all(
        p.hotel_match_basis == p.restaurant_match_basis == "exact_fields" for p in response.pairs
    )
    assert all(p.walk.night_access_verified is False for p in response.pairs)


def test_shared_hotel_is_resolved_once_but_each_branch_gets_own_route(tmp_path):
    svc, result, client, _, _ = setup(tmp_path, shared_hotel=True)
    response = svc.batch(batch(result))
    assert sum("酒店" in c for c in client.calls) == 1
    assert len(client.walks) == 3 and len(client.drives) == 1
    assert response.map_http_requests == 8


def test_ambiguous_hotel_requires_its_own_confirmation(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.hotel_mismatch = True
    response = svc.batch(batch(result))
    pair = response.pairs[0]
    assert all(p.status == "needs_confirmation" for p in response.pairs) and not client.walks
    with pytest.raises(BranchWalkError, match="WALK_IDENTITY_REQUIRED"):
        svc.route(route_request(result, pair, confirm_restaurant=True))
    verified = svc.route(route_request(result, pair, confirm_hotel=True))
    assert (
        verified.hotel_match_basis == "user_confirmed"
        and verified.restaurant_match_basis == "exact_fields"
    )


def test_restaurant_confirmation_cannot_be_replaced_by_hotel_confirmation(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.restaurant_mismatch = True
    pair = svc.batch(batch(result)).pairs[0]
    with pytest.raises(BranchWalkError, match="WALK_IDENTITY_REQUIRED"):
        svc.route(route_request(result, pair, confirm_hotel=True))
    assert not client.walks
    verified = svc.route(route_request(result, pair, confirm_restaurant=True))
    assert verified.restaurant_match_basis == "user_confirmed"


def test_same_restaurant_fields_but_different_source_id_requires_confirmation(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.changed_restaurant_id = True
    pair = svc.batch(batch(result)).pairs[0]
    assert pair.restaurant_matching.status == "unverified" and not client.walks
    assert (
        svc.route(route_request(result, pair, confirm_restaurant=True)).status == "route_verified"
    )


def test_options_cannot_cross_pair_role_or_recall_context(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.hotel_mismatch = True
    response = svc.batch(batch(result))
    one, two = response.pairs[:2]
    invalid = route_request(result, one, confirm_hotel=True).model_copy(
        update={"hotel_option_id": two.hotels[0].id}
    )
    with pytest.raises(BranchWalkError, match="WALK_OPTION_MISMATCH"):
        svc.route(invalid)
    invalid = route_request(result, one, confirm_hotel=True).model_copy(
        update={"hotel_option_id": one.restaurants[0].id}
    )
    with pytest.raises(BranchWalkError, match="WALK_OPTION_MISMATCH"):
        svc.route(invalid)
    invalid = route_request(result, one, confirm_hotel=True).model_copy(
        update={"request_id": "other"}
    )
    with pytest.raises(BranchWalkError, match="WALK_CONTEXT_MISMATCH"):
        svc.route(invalid)
    assert not client.walks


def test_expired_points_never_query_and_recheck_gets_new_refs(tmp_path):
    svc, result, client, clock, _ = setup(tmp_path)
    client.hotel_mismatch = True
    pair = svc.batch(batch(result)).pairs[0]
    clock[0] = 121
    with pytest.raises(BranchWalkError, match="WALK_CONTEXT_EXPIRED"):
        svc.route(route_request(result, pair, confirm_hotel=True))
    assert not client.walks
    new = svc.batch(batch(result)).pairs[0]
    assert new.context_id != pair.context_id and new.hotels[0].id != pair.hotels[0].id


def test_expiry_during_route_cannot_be_a_verified_result(tmp_path):
    svc, result, client, clock, _ = setup(tmp_path)
    client.hotel_mismatch = True
    pair = svc.batch(batch(result)).pairs[0]
    clock[0] = 119
    client.on_walk = lambda: clock.__setitem__(0, 121)
    with pytest.raises(BranchWalkError, match="WALK_CONTEXT_EXPIRED"):
        svc.route(route_request(result, pair, confirm_hotel=True))


def test_cancel_before_start_and_during_lookup_prevents_more_calls(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    req = batch(result)
    svc.cancel(req.operation_id)
    assert svc.batch(req).error.code == "CANCELLED" and not client.calls
    req = batch(result)
    client.on_search = lambda: svc.cancel(req.operation_id)
    response = svc.batch(req)
    assert response.error.code == "CANCELLED" and len(client.calls) == 1 and not client.walks
    assert all(p.walk is None and p.context_id is None for p in response.pairs)


def test_source_auth_failure_stops_without_automatic_retry_or_fallback(tmp_path):
    svc, result, client, _, factory = setup(tmp_path)

    def denied():
        raise ProbeError("ACCESS_DENIED")

    client.on_search = denied
    response = svc.batch(batch(result))
    assert len(client.calls) == 1 and factory == ["amap"]
    assert response.error.code == "ACCESS_DENIED" and not client.walks


def test_all_pair_failures_and_partial_success_stay_distinct(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)

    def intermittent():
        if len(client.walks) == 2:
            raise ProbeError("TIMEOUT")

    client.on_walk = intermittent
    response = svc.batch(batch(result))
    assert response.status == "partial" and [p.status for p in response.pairs] == [
        "route_verified",
        "unavailable",
        "route_verified",
    ]
    assert response.pairs[1].context_id is None and response.pairs[1].walk is None


def test_route_failure_does_not_return_a_previous_walk_as_fresh(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    pair = svc.batch(batch(result)).pairs[0]

    def failed():
        raise ProbeError("TIMEOUT")

    client.on_walk = failed
    refreshed = svc.route(route_request(result, pair))
    assert refreshed.status == "unavailable" and refreshed.walk is None
    assert refreshed.walk_limit_status == "unverified"


def test_duplicate_operation_is_not_replayed_and_extra_coordinates_are_rejected(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    req = batch(result)
    svc.batch(req)
    count = len(client.calls)
    with pytest.raises(BranchWalkError, match="WALK_OPERATION_CONFLICT"):
        svc.batch(req)
    assert len(client.calls) == count
    with pytest.raises(ValidationError):
        BranchWalkBatchRequest(**req.model_dump(), latitude=1)


def test_recall_membership_and_expiry_authority_precede_any_maps(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    svc.recall.associations.clear()
    from stay_agent.recall import RecallError

    with pytest.raises(RecallError, match="RECALL_ASSOCIATION_NOT_FOUND"):
        svc.batch(batch(result))
    assert not client.calls


def test_transport_cancel_is_checked_without_network():
    import threading

    http = SecureHTTP("amap", "synthetic-map-key")
    http.cancelled = threading.Event()
    http.cancelled.set()
    with pytest.raises(ProbeError, match="CANCELLED"):
        http.remaining()
    assert http.request_count == 0


def test_api_schema_and_no_store(tmp_path):
    svc, result, _, _, _ = setup(tmp_path)
    conv = SimpleNamespace(recover=lambda: None, close=lambda: None)
    app = create_app(
        recall_service=svc.recall,
        service=QuoteService(),
        session_store=svc.sessions,
        branch_walk_service=svc,
        conversation_service=conv,
        web_root=tmp_path / "none",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/branch-walk/batch", json=batch(result).model_dump())
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["data_mode"] == "test" and response.json()["processed_pairs"] == 3
        assert client.post("/api/branch-walk/not-a-valid-id/cancel").status_code == 422


@pytest.mark.parametrize("provider", ["amap", "tencent"])
def test_distance_1500_and_drive_900_are_inclusive_but_next_unit_fails(tmp_path, provider):
    svc, result, client, _, factory = setup(tmp_path, provider=provider)
    client.meters = [1500, 1501, 1501]
    client.seconds = [1600, 100, 300]
    client.drive_seconds = [900, 901]
    response = svc.batch(batch(result))
    assert response.status == "complete"
    assert [p.walk_limit_status for p in response.pairs] == [
        "within_1500", "over_1500", "over_1500",
    ]
    assert [p.drive_limit_status for p in response.pairs] == [
        "unverified", "within_900", "over_900",
    ]
    assert [p.transport_status for p in response.pairs] == [
        "pass_walk_or_drive", "pass_walk_or_drive", "rejected_both_modes_over_limit",
    ]
    assert response.pairs[0].drive is None
    assert [p.drive.duration_seconds for p in response.pairs[1:]] == [900, 901]
    assert all(
        p.drive.pickup_wait_verified is False and p.drive.future_traffic_verified is False
        for p in response.pairs[1:]
    )
    assert factory == [provider]
    assert response.map_http_requests == 11  # 6 identity searches + 3 walks + 2 drives


def test_missing_drive_is_unknown_not_two_modes_rejected(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.meters = [1501, 1501, 1501]
    client.empty_drive_indices = {0}
    client.drive_seconds = [901, 900, 901]
    response = svc.batch(batch(result))
    assert [p.transport_status for p in response.pairs] == [
        "unknown_or_partial", "pass_walk_or_drive", "rejected_both_modes_over_limit",
    ]
    assert response.pairs[0].walk_limit_status == "over_1500"
    assert response.pairs[0].drive_limit_status == "unverified"
    assert response.pairs[0].status == "route_verified"
    assert response.map_http_requests == 12


def test_missing_walk_may_pass_drive_without_claiming_walk_failure(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.empty_walk_indices = {0}
    client.drive_seconds = [900, 901]
    response = svc.batch(batch(result))
    first = response.pairs[0]
    assert first.walk is None and first.walk_limit_status == "unverified"
    assert first.drive is not None and first.drive_limit_status == "within_900"
    assert first.transport_status == "pass_walk_or_drive"
    assert response.map_http_requests == 11


def test_both_missing_routes_do_not_create_positive_or_stale_map_context(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.empty_walk_indices = {0}
    client.empty_drive_indices = {0}
    response = svc.batch(batch(result))
    first = response.pairs[0]
    assert first.status == "skipped"
    assert first.context_id is None
    assert first.walk is None and first.drive is None
    assert first.transport_status == "unknown_or_partial"
    assert response.status == "partial"


def test_cancellation_during_drive_stops_before_second_pair_and_loses_result(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.meters = [1501, 1501, 1501]
    request = batch(result)
    client.on_drive = lambda: svc.cancel(request.operation_id)
    response = svc.batch(request)
    assert response.error.code == "CANCELLED"
    assert len(client.calls) == 2  # original hotel + restaurant identities only
    assert len(client.walks) == 1 and len(client.drives) == 1
    assert all(
        p.walk is None and p.drive is None and p.transport_status == "unknown_or_partial"
        for p in response.pairs
    )


def test_expiry_during_drive_cannot_return_a_verified_route(tmp_path):
    svc, result, client, clock, _ = setup(tmp_path)
    client.hotel_mismatch = True
    client.meters = [1501, 1501, 1501]
    pair = svc.batch(batch(result)).pairs[0]
    clock[0] = 119
    client.on_drive = lambda: clock.__setitem__(0, 121)
    with pytest.raises(BranchWalkError, match="WALK_CONTEXT_EXPIRED"):
        svc.route(route_request(result, pair, confirm_hotel=True))
    assert len(client.drives) == 1
    assert svc.contexts[pair.context_id].pair.walk is None
    assert svc.contexts[pair.context_id].pair.drive is None


def test_exactly_twelve_map_requests_on_all_three_overlong_walks(tmp_path):
    svc, result, client, _, _ = setup(tmp_path)
    client.meters = [1501, 1501, 1501]
    response = svc.batch(batch(result))
    assert response.map_http_requests == 12
    assert len(client.calls) == 6
    assert len(client.walks) == len(client.drives) == 3
    assert all(p.transport_status in (
        "pass_walk_or_drive", "rejected_both_modes_over_limit",
    ) for p in response.pairs)
