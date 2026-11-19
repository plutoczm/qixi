"""R102 offline guardrails: user-confirmed location, bounded source calls, no price spoofing."""
from datetime import date, timedelta

from fastapi.testclient import TestClient
import pytest

from stay_agent.contracts import (
    NearbyResolveRequest, NearbyExploreRequest, NearbyWalkRequest,
)
from stay_agent.poi_nearby import NearbyPlaceService
from stay_agent.providers.data.travel_clients import Point
from stay_agent.storage import SessionStore


ANCHOR = Point("amap", "poi-disney", "上海迪士尼乐园", 121.655, 31.142)
ALTERNATE = Point("amap", "poi-other", "其他地点", 121.656, 31.141)
HOTEL_1 = Point("amap", "h-1", "花园酒店上海迪士尼店", 121.656, 31.142)
HOTEL_2 = Point("amap", "h-2", "度假酒店二号", 121.659, 31.142)
OUT_OF_CITY = Point("amap", "h-3", "异城酒店", 121.658, 31.142)
OVER_RADIUS = Point("amap", "h-4", "过远酒店", 121.680, 31.142)


class MapStub:
    def __init__(self):
        self.calls = []
        self.metadata = {
            ANCHOR.poi_id: {"city": "上海市", "address": "上海市浦东新区"},
            ALTERNATE.poi_id: {"city": "上海市", "address": "上海市浦东新区其他路"},
            HOTEL_1.poi_id: {"city": "上海市", "address": "浦东新区繁荣路 12 号"},
            HOTEL_2.poi_id: {"city": "上海市", "address": "浦东新区美好路 8 号"},
            OUT_OF_CITY.poi_id: {"city": "北京市", "address": "北城区"},
            OVER_RADIUS.poi_id: {"city": "上海市", "address": "远方路 88 号"},
        }

    def search(self, keyword, city, *, page=1, page_size=5):
        self.calls.append(("search", keyword, city, page, page_size))
        return [ANCHOR, ALTERNATE]

    def around(self, point, keyword, limit):
        assert point is ANCHOR and keyword == "酒店" and limit == 10
        self.calls.append(("around", point.poi_id))
        return [HOTEL_2, OUT_OF_CITY, HOTEL_1, OVER_RADIUS]

    def walk(self, start, end):
        assert start is ANCHOR and end in (HOTEL_1, HOTEL_2)
        self.calls.append(("walk", start.poi_id, end.poi_id))
        return [{"distance_meters": 650, "duration_seconds": 540}]


class SourceSessions:
    def __init__(self, *, place="上海", place_type="城市", names=None):
        self.calls = 0
        self.place = place
        self.place_type = place_type
        self.names = names or [
            {"id": 101, "name": "花园酒店上海迪士尼店", "address": "浦东新区繁荣路12号"},
        ]

    def list_hotels(self, request_id):
        assert request_id == "test-search-1"
        self.calls += 1
        return {"place": self.place, "place_type": self.place_type, "country_region": "中国大陆"}, self.names


@pytest.fixture
def scene():
    clock = [1000]
    maps = MapStub()
    service = NearbyPlaceService(
        factories={"amap": lambda: maps}, clock=lambda: clock[0], data_mode="test",
    )
    return service, maps, clock


def _resolved(scene):
    service, maps, _ = scene
    data = service.resolve(NearbyResolveRequest(
        provider="amap", keyword="上海迪士尼", city="上海", country_region="中国大陆",
    ))
    assert data["status"] == "complete" and len(data["options"]) == 2
    assert len(maps.calls) == 1 and data["context_id"]
    assert "poi-disney" not in str(data)
    return data


def test_parallel_nearby_checks_each_source_against_the_same_map_snapshot(scene, tmp_path):
    service, maps, _ = scene
    resolved = _resolved(scene)
    store = SessionStore(tmp_path / "parallel-nearby.sqlite")
    query = {"place": "上海", "place_type": "城市", "poi_name": ANCHOR.title,
             "country_region": "中国大陆"}
    hotel = {"id": 101, "name": HOTEL_1.title, "address": "上海市浦东新区繁荣路12号"}
    maps.metadata[HOTEL_1.poi_id]["address"] = hotel["address"]
    store.save("rollinggo-search", query, [101], [hotel], supplier_source="rollinggo")
    store.save("tuniu-search", query, [101], [hotel], supplier_source="tuniu")
    store.save("wrong-anchor", {**query, "poi_name": ALTERNATE.title}, [101], [hotel], supplier_source="tuniu")
    result = service.explore(NearbyExploreRequest(
        context_id=resolved["context_id"], anchor_id=resolved["options"][0]["id"],
        request_id="rollinggo-search",
    ), store, recommendation_requests=("rollinggo-search", "tuniu-search", "wrong-anchor"))
    point = next(point for point in result["hotels"] if point["name"] == HOTEL_1.title)
    assert result["recommendation_links"] == [
        {"request_id": request_id, "hotel_id": 101, "hotel_option_id": point["id"]}
        for request_id in ("rollinggo-search", "tuniu-search")
    ]
    assert [call[0] for call in maps.calls] == ["search", "around"]


def test_explicit_overseas_and_unknown_city_reject_without_map_or_supplier(scene):
    service, maps, _ = scene
    rejected = service.resolve(NearbyResolveRequest(
        provider="amap", keyword="东京新宿御苑", city="东京", country_region="日本"
    ))
    assert rejected["status"] == "unavailable"
    assert rejected["error"].code == "REGION_NOT_SUPPORTED"
    assert not maps.calls


def test_separate_point_selection_and_checked_map_radius(scene):
    service, maps, _ = scene
    resolved = _resolved(scene)
    anchor_id = resolved["options"][0]["id"]
    request = NearbyExploreRequest(context_id=resolved["context_id"], anchor_id=anchor_id)
    data = service.explore(request, SourceSessions())
    assert data["status"] == "complete"
    assert len(data["hotels"]) == 2
    assert data["hotels"][0]["name"] == HOTEL_1.title
    assert data["hotels"][0]["source_hotel_id"] is None
    assert data["hotels"][0]["source_match"] == "unverified"
    assert data["ranking_status"] == "not_comparable"
    assert len(maps.calls) == 2
    repeated = service.explore(request, SourceSessions())
    assert repeated["error"].code == "CONTEXT_LOCKED"
    assert len(maps.calls) == 2


def test_unique_three_field_match_links_only_same_supplier_member(scene):
    service, maps, _ = scene
    resolved = _resolved(scene)
    ids = (resolved["context_id"], resolved["options"][0]["id"])
    source = SourceSessions()
    data = service.explore(NearbyExploreRequest(
        context_id=ids[0], anchor_id=ids[1], request_id="test-search-1"
    ), source)
    assert source.calls == 1
    assert data["status"] == "complete"
    first = next(h for h in data["hotels"] if h["name"] == HOTEL_1.title)
    second = next(h for h in data["hotels"] if h["name"] == HOTEL_2.title)
    assert first["source_hotel_id"] == 101 and first["source_match"] == "matched"
    assert second["source_hotel_id"] is None
    assert first["review_count"] is None and first["stayed_count"] is None
    assert "price" not in first
    assert len(maps.calls) == 2


@pytest.mark.parametrize("place,place_type", [("北京", "城市"), ("上海迪士尼", "景点")])
def test_different_supplier_city_scope_cannot_inherit_map_identity(scene, place, place_type):
    service, _, _ = scene
    data = _resolved(scene)
    result = service.explore(
        NearbyExploreRequest(
            context_id=data["context_id"], anchor_id=data["options"][0]["id"],
            request_id="test-search-1",
        ),
        SourceSessions(place=place, place_type=place_type),
    )
    assert all(item["source_hotel_id"] is None for item in result["hotels"])


def test_duplicate_supplier_identity_does_not_merge_prices(scene):
    service, _, _ = scene
    data = _resolved(scene)
    source = SourceSessions(names=[
        {"id": 101, "name": HOTEL_1.title, "address": "浦东新区繁荣路12号"},
        {"id": 102, "name": HOTEL_1.title, "address": "浦东新区繁荣路12号"},
    ])
    result = service.explore(NearbyExploreRequest(
        context_id=data["context_id"], anchor_id=data["options"][0]["id"],
        request_id="test-search-1",
    ),source)
    hotel = next(h for h in result["hotels"] if h["name"] == HOTEL_1.title)
    assert hotel["source_match"] == "ambiguous" and hotel["source_hotel_id"] is None


def test_unknown_context_and_foreign_point_fail_before_source(scene):
    service, maps, _ = scene
    data = _resolved(scene)
    rejected = service.explore(NearbyExploreRequest(
        context_id=data["context_id"], anchor_id="0"*32,
    ), SourceSessions())
    assert rejected["error"].code == "POINT_NOT_IN_CONTEXT"
    assert len(maps.calls) == 1
    rejected = service.walk(NearbyWalkRequest(
        context_id="0"*32, hotel_option_id="2"*32,
    ))
    assert rejected["error"].code == "CONTEXT_NOT_FOUND"
    assert len(maps.calls) == 1


def test_120_second_expiry_rejects_old_points_without_source_calls(scene):
    service, maps, clock = scene
    data = _resolved(scene)
    clock[0] += 121
    stale = service.explore(NearbyExploreRequest(
        context_id=data["context_id"], anchor_id=data["options"][0]["id"],
    ),SourceSessions())
    assert stale["error"].code == "CONTEXT_EXPIRED"
    assert len(maps.calls) == 1


def test_explicit_three_walk_request_limit_and_route_source(scene):
    service, maps, _ = scene
    data = _resolved(scene)
    near = service.explore(NearbyExploreRequest(
        context_id=data["context_id"], anchor_id=data["options"][0]["id"],
    ),SourceSessions())
    hotel_id = near["hotels"][0]["id"]
    route = NearbyWalkRequest(context_id=data["context_id"],hotel_option_id=hotel_id)
    for _ in range(3):
        response = service.walk(route)
        assert response["status"] == "complete"
        assert response["distance_meters"] == 650
        assert response["duration_seconds"] == 540
    count = len(maps.calls)
    response = service.walk(route)
    assert response["error"].code == "REQUEST_LIMIT"
    assert len(maps.calls) == count


def test_source_search_missing_fails_before_spending_map_request(scene):
    service, maps, _ = scene
    data = _resolved(scene)
    class Expired:
        def list_hotels(self, request_id):
            raise AttributeError("missing session")
    response = service.explore(NearbyExploreRequest(
        context_id=data["context_id"], anchor_id=data["options"][0]["id"],
        request_id="test-search-1",
    ), Expired())
    assert response["error"].code == "SOURCE_SESSION_MISSING"
    assert len(maps.calls) == 1


def test_real_session_store_exposes_only_fresh_members(tmp_path):
    store = SessionStore(tmp_path/"isolated.sqlite3")
    scope = {
        "place": "上海", "place_type": "城市", "country_region": "中国大陆",
        "check_in": str(date.today() + timedelta(days=1)),
    }
    store.save("test-search-1", scope, [101], hotel_metadata=[
        {"id": 101, "name": HOTEL_1.title, "address": "浦东新区繁荣路12号"},
        {"id": 102, "name": "不应出现", "address": "未知"},
    ])
    query, rows = store.list_hotels("test-search-1")
    assert query == scope
    assert rows == [{"id": 101, "name": HOTEL_1.title, "address": "浦东新区繁荣路12号"}]


def test_http_three_step_contract_and_validation(tmp_path, scene):
    from stay_agent.main import create_app
    from stay_agent.service import QuoteService

    nearby, maps, _ = scene
    app = create_app(
        service=QuoteService(client_factory=lambda: None),
        session_store=SessionStore(tmp_path/"http-sessions.sqlite3"),
        nearby_service=nearby, web_root=tmp_path/"web",
    )
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    invalid = client.post("/api/places/resolve", json={
        "provider": "amap", "keyword": "地点", "city": ""
    })
    assert invalid.status_code == 422 and not maps.calls
    abroad = client.post("/api/places/resolve", json={
        "provider": "amap", "keyword": "东京站", "city": "东京", "country_region": "日本",
    })
    assert abroad.status_code == 200
    assert abroad.json()["error"]["code"] == "REGION_NOT_SUPPORTED"
    assert not maps.calls
    checked = client.post("/api/places/resolve", json={
        "provider": "amap", "keyword": "上海迪士尼", "city": "上海", "country_region": "中国大陆",
    })
    assert checked.status_code == 200
    body = checked.json()
    assert body["status"] == "complete" and len(maps.calls) == 1
    assert "lat" not in body and "poi-disney" not in str(body)
    rejected = client.post("/api/places/nearby", json={
        "context_id": body["context_id"], "anchor_id": "0"*32,
    })
    assert rejected.json()["error"]["code"] == "POINT_NOT_IN_CONTEXT"
    assert len(maps.calls) == 1
    nearby_response = client.post("/api/places/nearby", json={
        "context_id": body["context_id"], "anchor_id": body["options"][0]["id"],
    })
    assert nearby_response.status_code == 200
    hotels = nearby_response.json()["hotels"]
    assert len(hotels) == 2
    assert all(hotel["source_hotel_id"] is None for hotel in hotels)
    walking = client.post("/api/places/walk", json={
        "context_id": body["context_id"], "hotel_option_id": hotels[0]["id"],
    })
    assert walking.status_code == 200
    assert walking.json()["status"] == "complete"
    assert walking.json()["distance_meters"] == 650
    assert len(maps.calls) == 3


def test_non_mainland_adcode_is_filtered_before_point_selection(scene):
    service,maps,_=scene
    maps.metadata[ANCHOR.poi_id]["adcode"]="810000"
    result=service.resolve(NearbyResolveRequest(
        provider="amap", keyword="上海迪士尼", city="上海", country_region="中国大陆",
    ))
    assert result["status"]=="complete"
    assert [option["name"] for option in result["options"]]==[ALTERNATE.title]


def test_non_mainland_hotel_adcode_is_not_shown_or_bound_to_price(scene):
    service,maps,_=scene
    maps.metadata[HOTEL_2.poi_id]["adcode"]="820000"
    result=_resolved(scene)
    data=service.explore(NearbyExploreRequest(
        context_id=result["context_id"],anchor_id=result["options"][0]["id"],
    ),SourceSessions())
    assert data["status"]=="complete"
    assert [hotel["name"] for hotel in data["hotels"]]==[HOTEL_1.title]


def test_non_mainland_city_text_is_filtered_even_without_adcode(scene):
    service,maps,_=scene
    maps.metadata[ALTERNATE.poi_id]["city"]="香港特别行政区"
    result=service.resolve(NearbyResolveRequest(
        provider="amap", keyword="上海迪士尼", city="上海", country_region="中国大陆",
    ))
    assert [option["name"] for option in result["options"]]==[ANCHOR.title]
