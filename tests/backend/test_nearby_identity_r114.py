"""Offline missing-city manual identity, local revision/intent and expiry fences."""
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from stay_agent.contracts import NearbyExploreRequest, NearbyResolveRequest, SearchInput, china_today
from stay_agent.main import create_app
from stay_agent.poi_nearby import NearbyPlaceService
from stay_agent.providers.data.travel_clients import Point
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore


ANCHOR = Point("amap", "secret-anchor", "测试目的地", 121.65, 31.14)
HOTEL = Point("amap", "secret-hotel", "测试花园酒店(一店)", 121.651, 31.14)
SECOND = Point("amap", "secret-second", HOTEL.title, 121.652, 31.14)
RAW = {"hotelId": 7, "name": HOTEL.title, "address": "繁荣路12号", "areaCode": "CN",
       "price": {"hasPrice": True, "currency": "CNY", "lowestPrice": 88}}


@pytest.fixture
def build(tmp_path):
    def make(*, raw=None, adcode="310115", duplicate_map=False, ttl=1800, observed=True):
        epoch, mono = [1700000000.0], [1000.0]
        store = SessionStore(tmp_path / (uuid4().hex + ".db"), ttl_seconds=ttl, clock=lambda: epoch[0])
        class Source:
            calls = 0
            def call_tool(self, name, arguments):
                self.calls += 1
                assert name == "searchHotels", "manual identity must not query a source"
                return {"structuredContent": {"success": True, "hotelInformationList": raw or [RAW]}}
            def redact(self, value):
                return value
        class Maps:
            calls = 0
            metadata = {
                ANCHOR.poi_id: {"city": "上海市", "address": "浦东新区", "adcode": "310115"},
                HOTEL.poi_id: {"city": "上海市", "address": RAW["address"], "adcode": adcode},
                SECOND.poi_id: {"city": "上海市", "address": RAW["address"], "adcode": adcode},
            }
            def search(self, *args, **kwargs):
                self.calls += 1
                return [ANCHOR]
            def around(self, *args, **kwargs):
                self.calls += 1
                return [HOTEL, SECOND] if duplicate_map else [HOTEL]
            def walk(self, *args):
                self.calls += 1
                return [{"distance_meters": 160, "duration_seconds": 120}]
        source, maps = Source(), Maps()
        service = QuoteService(client_factory=lambda: source)
        service.data_mode = "test"
        query = SearchInput(place="上海", place_type="城市", country_region="中国大陆",
                            poi_name=ANCHOR.title, check_in=china_today() + timedelta(days=1),
                            check_out=china_today() + timedelta(days=2), adults=2, rooms=1)
        search = service.search(query, store)
        near = NearbyPlaceService(factories={"amap": lambda: maps}, clock=lambda: mono[0],
                                  data_mode="test", city_observations=service.city_observations if observed else None)
        resolved = near.resolve(NearbyResolveRequest(keyword=ANCHOR.title, city="上海", provider="amap"))
        common = {"context_id": resolved["context_id"], "anchor_id": resolved["options"][0]["id"],
                  "request_id": search.request_id}
        found = near.explore(NearbyExploreRequest(**common), store)
        app = create_app(service=service, session_store=store, nearby_service=near, web_root=tmp_path / "web")
        # An intentionally unobserved instance must remain unobserved even with SQL rows.
        if not observed:
            near.city_observations = None
        client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=True)
        option = found["hotels"][0]["id"] if found["hotels"] else "e" * 32
        operation = {**common, "hotel_option_id": option, "source_hotel_id": 7,
                     "expected_revision": 0, "intent_seq": 1, "operation_id": uuid4().hex}
        return client, common, operation, near, store, epoch, mono, maps, source, found
    return make


def status(scene):
    return scene[0].post("/api/places/identity/status", json=scene[1])


def confirm(scene, **changes):
    return scene[0].post("/api/places/identity/confirm", json={**scene[2], "confirmed": True, **changes})


def revoke(scene, **changes):
    return scene[0].post("/api/places/identity/revoke", json={**scene[2], "operation_id": uuid4().hex,
        "intent_seq": 2, "target_operation_id": scene[2]["operation_id"], **changes})


def test_missing_city_explicit_local_confirmation_and_revoke(build):
    scene = build()
    initial = status(scene).json()
    assert initial["status"] == "complete" and initial["revision"] == 0
    pair = initial["eligible_pairs"][0]
    assert pair["source_city_status"] == "missing" and pair["map_city"] == "上海市"
    assert pair["source_name"] == HOTEL.title and pair["source_address"] == RAW["address"]
    assert pair["source_observed_at"]
    assert scene[-1]["hotels"][0]["source_match"] == "unverified"
    done = confirm(scene).json()
    assert done["bindings"][0]["identity_basis"] == "user_confirmed"
    assert done["revision"] == 1
    again = confirm(scene).json()
    assert again["revision"] == 1
    removed = revoke(scene, expected_revision=1).json()
    assert removed["status"] == "complete" and removed["revision"] == 2
    assert not removed["bindings"]
    assert not confirm(scene).json()["bindings"]  # duplicate cannot resurrect
    assert scene[7].calls == 2 and scene[8].calls == 1
    assert scene[3].contexts[scene[1]["context_id"]].route_attempts == 0
    assert "secret-" not in str(done) and "310115" not in str(done)


@pytest.mark.parametrize("fields", [
    {"city": "上海"}, {"cityName": "北京"}, {"cityCode": "opaque"},
    {"city": []}, {"city": False}, {"areaCode": None}, {"areaCode": "HK"},
    {"countryCode": "US"}, {"name": "另一分店"}, {"address": "繁荣路13号"},
])
def test_only_city_missing_can_be_overridden(build, fields):
    scene = build(raw=[RAW | fields])
    assert not status(scene).json()["eligible_pairs"]
    assert confirm(scene).status_code == 409
    assert scene[7].calls == 2 and scene[8].calls == 1


@pytest.mark.parametrize("code", [None, "", "000000", "999999", "810101", "440106", True, []])
def test_positive_same_city_mainland_code_required(build, code):
    scene = build(adcode=code)
    assert not status(scene).json()["eligible_pairs"]
    assert confirm(scene).status_code == 409


def test_observation_unknown_and_duplicate_identity_block(build):
    for scene in (build(observed=False), build(duplicate_map=True),
                  build(raw=[RAW, RAW | {"hotelId": 8}]),
                  build(raw=[RAW, RAW | {"name": "同ID冲突"}])):
        assert not status(scene).json()["eligible_pairs"]
        assert confirm(scene).status_code == 409


def test_bound_original_query_member_provider_and_session_cannot_change(build):
    scene = build()
    assert confirm(scene, request_id="another-session").status_code == 409
    assert status(scene).json()["eligible_pairs"]
    confirm(scene)
    scene[4].replace_membership(scene[1]["request_id"], [7], [{"id": 7, "name": HOTEL.title, "address": "换址"}])
    response = status(scene)
    assert response.status_code == 409 and not response.json()["bindings"]
    assert confirm(scene, expected_revision=1, intent_seq=2, operation_id=uuid4().hex).status_code == 409


def test_two_clocks_and_original_source_ttl_are_not_extended(build):
    scene = build(ttl=45)
    assert status(scene).json()["expires_in_seconds"] == 45
    scene[5][0] += 20
    scene[6][0] += 20
    assert confirm(scene).json()["expires_in_seconds"] == 25
    scene[5][0] += 25
    scene[6][0] += 25
    response = status(scene)
    assert response.status_code == 410 and not response.json()["bindings"]
    scene = build()
    scene[5][0] += 119
    scene[6][0] += 119
    assert confirm(scene).json()["expires_in_seconds"] == 1
    scene[5][0] += 1
    scene[6][0] += 1
    assert revoke(scene, expected_revision=1).status_code == 410


def test_revoke_tombstone_rejects_late_confirm(build):
    scene = build()
    assert revoke(scene).json()["revision"] == 1
    late = confirm(scene)
    assert late.status_code == 409 and not late.json()["bindings"]
    assert status(scene).json()["revision"] == 1


def test_revision_conflict_must_sync_and_revoke_exact_original_operation(build):
    scene = build()
    confirm(scene)
    conflict = revoke(scene)
    assert conflict.status_code == 409 and conflict.json()["status"] == "unavailable"
    current = status(scene).json()
    assert current["bindings"][0]["operation_id"] == scene[2]["operation_id"]
    assert revoke(scene, expected_revision=current["revision"]).json()["bindings"] == []
    new_op = uuid4().hex
    assert confirm(scene, operation_id=new_op, intent_seq=3, expected_revision=2).json()["revision"] == 3
    old_revoke = revoke(scene, expected_revision=3, intent_seq=4)
    assert old_revoke.status_code == 409
    assert status(scene).json()["bindings"][0]["operation_id"] == new_op


@pytest.mark.parametrize("fields", [{"confirmed": False}, {"confirmed": 1}, {"confirmed": "true"},
                                    {"source_hotel_id": True}, {"intent_seq": True}])
def test_contract_requires_strict_explicit_confirmation(build, fields):
    scene = build()
    assert confirm(scene, **fields).status_code == 422
    assert not status(scene).json()["bindings"]


def test_same_pair_cannot_silently_overwrite_and_operation_ids_bind_payload(build):
    scene = build()
    assert confirm(scene).status_code == 200
    assert confirm(scene, operation_id=uuid4().hex, intent_seq=2, expected_revision=1).status_code == 409
    assert confirm(scene, intent_seq=3, expected_revision=1).status_code == 409
    assert status(scene).json()["bindings"][0]["operation_id"] == scene[2]["operation_id"]


def test_context_lock_admits_only_one_transition_and_does_not_consume_route_budget(build):
    scene = build()
    ctx = scene[3].contexts[scene[1]["context_id"]]
    with ctx.lock:
        assert confirm(scene).status_code == 503
    assert not status(scene).json()["bindings"]
    assert ctx.identity.revision == 0 and ctx.route_attempts == 0
    assert scene[7].calls == 2 and scene[8].calls == 1


def test_automatic_matches_are_not_available_for_manual_confirmation(build):
    scene = build(raw=[RAW | {"address": "上海市繁荣路12号"}])
    ctx = scene[3].contexts[scene[1]["context_id"]]
    # Existing automatic mapping is a reserved source member, irrespective of UI.
    ctx.hotel_rows[scene[2]["hotel_option_id"]].update(source_match="matched", source_hotel_id=7)
    assert not status(scene).json()["eligible_pairs"]
    assert confirm(scene).status_code == 409


def test_confirm_revoke_never_reset_map_deadline_counts_or_three_walks(build):
    scene = build()
    maps = scene[7]
    from types import SimpleNamespace
    maps.http = SimpleNamespace(deadline=12345, total_timeout=60, count=9)
    ctx = scene[3].contexts[scene[1]["context_id"]]
    ctx.route_attempts = 3
    confirm(scene)
    revoke(scene, expected_revision=1)
    assert maps.http.deadline == 12345 and maps.http.count == 9
    response = scene[0].post("/api/places/walk", json={"context_id": scene[1]["context_id"],
        "hotel_option_id": scene[2]["hotel_option_id"]})
    assert response.json()["error"]["code"] == "REQUEST_LIMIT"
    assert maps.calls == 2 and ctx.route_attempts == 3


def test_bounded_operation_tombstones_do_not_evict_earlier_intents(build):
    scene = build()
    for index in range(64):
        response = revoke(scene, operation_id=uuid4().hex, intent_seq=index+1, expected_revision=index)
        assert response.status_code == 200
    assert revoke(scene, intent_seq=65, expected_revision=64).json()["error"]["code"] == "IDENTITY_OPERATION_LIMIT"
    assert confirm(scene).status_code == 409
    assert not status(scene).json()["bindings"]
