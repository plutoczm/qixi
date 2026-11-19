"""One-named-hotel production pipeline with synthetic providers only: NO external calls."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stay_agent.contracts import RecallInput, SearchInput, NamedHotel, china_today
from stay_agent.main import create_app
from stay_agent.recall import RecallService
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore
from stay_agent.providers.data.rollinggo_client import RollingGoError
from test_recall import Quotes, hotel
from test_branch_walk import Map as WalkMap
from stay_agent.branch_walk import BranchWalkService

RESTAURANT = "海底捞火锅（荔胜广场店）"
RESTAURANT_ADDRESS = "花地大道中66号荔胜广百广场6楼"
HOME = "如家酒店·neo（广州芳村花地湾地铁站店）"
HOME_ADDRESS = "广州市荔湾区东漖北路566-568号"


def request(**replace):
    today = china_today()
    query = SearchInput(
        place="广州", check_in=today + timedelta(days=6),
        check_out=today + timedelta(days=7), adults=2, rooms=1,
        children_ages=[], budget_fen=20000, limit=20,
    )
    props = {
        "strategy": "named_hotel", "operation_id": uuid4().hex,
        "provider": "amap", "query": query, "branch_limit": 3,
        "restaurant_keyword": RESTAURANT,
        "restaurant_address": RESTAURANT_ADDRESS,
        "named_hotel": NamedHotel(name=HOME, address=HOME_ADDRESS),
    }
    return RecallInput(**{**props, **replace})


class Maps:
    def __init__(self, *, restaurant=None, hotels=None, map_city="广州市",
                 category="住宿服务;宾馆酒店;宾馆酒店", hotel_code="100100"):
        self.restaurants = restaurant if restaurant is not None else [
            SimpleNamespace(poi_id="R1", title="海底捞火锅(荔胜广场店)")
        ]
        self.hotels = hotels if hotels is not None else [
            SimpleNamespace(poi_id="H1", title="如家酒店·neo(广州芳村花地湾地铁站店)")
        ]
        self.metadata = {
            "R1": {"address": RESTAURANT_ADDRESS, "city": "广州市",
                   "type": "餐饮服务;中餐厅;火锅店", "typecode": "050117"},
        }
        for point in self.hotels:
            self.metadata[point.poi_id] = {
                "address": "荔湾区东漖北路566-568号", "city": map_city,
                "type": category, "typecode": hotel_code,
            }
        self.calls = []
        self.last_search = {}
        self.http = SimpleNamespace(request_count=0)

    def search(self, keyword, city, page, page_size):
        self.http.request_count += 1
        self.calls.append((keyword, city, page, page_size))
        rows = self.restaurants if keyword == RESTAURANT else self.hotels
        self.last_search = {"raw_count": len(rows), "valid_count": len(rows)}
        return rows

    def around(self, *args, **kwargs):
        raise AssertionError("NO_NEARBY_LOOKUP_IN_NAMED_MODE")


def setup(tmp_path, maps=None, output=None):
    maps = maps or Maps()
    quotes = Quotes([output if output is not None else [
        hotel(427406, name=HOME, address=HOME_ADDRESS, price=172)
    ]])
    service = RecallService(
        quote_service=QuoteService(),
        map_factory=lambda provider, deadline: maps,
        quote_factory=lambda deadline: quotes,
        data_mode="test",
    )
    session = SessionStore(tmp_path / "named-search.sqlite3")
    return service, maps, quotes, session


def test_named_full_name_address_one_map_one_supplier_with_source_city_unverified(tmp_path):
    svc, maps, quotes, session = setup(tmp_path)
    response = svc.search(request(), session)
    assert response.strategy == "named_hotel"
    assert response.status == "complete"
    assert response.data_mode == "test"
    assert [h.name for h in response.hotels] == [HOME]
    assert response.walk_check_available is True
    assert [call[0] for call in maps.calls] == [RESTAURANT, HOME]
    assert len(maps.calls) == 2
    assert response.recall_coverage.map_http_requests == 2
    assert response.recall_coverage.named_hotel_map_lookups == 1
    assert response.recall_coverage.named_hotel_map_matches == 1
    assert response.recall_coverage.nearby_branches_attempted == 0
    assert response.recall_coverage.hotel_lookups_attempted == 1
    assert response.recall_coverage.quote_http_requests == 1
    assert response.continuation_cursor is None
    assert len(quotes.arguments) == 1
    supplier_args = quotes.arguments[0]
    assert supplier_args["place"] == HOME and supplier_args["placeType"] == "酒店"
    assert supplier_args["hotelTags"]["maxPricePerNight"] == 200
    assert supplier_args["checkInParam"]["adultCount"] == 2
    assert supplier_args["checkInParam"]["stayNights"] == 1
    target = response.branches[0].hotel_targets[0]
    assert target.status == "retained" and target.match_status == "unverified"
    assert target.reason == "source_city_unverified"
    assert target.hotel_id == 427406
    assert session.load(response.request_id, 427406)["rooms"] == 1
    anchor = svc.association(response.request_id)["branches"][0]["hotel_targets"][427406]
    assert anchor["source_id"] == "H1"
    assert anchor["name"] == "如家酒店·neo(广州芳村花地湾地铁站店)"
    assert anchor["address"] == "荔湾区东漖北路566-568号"


@pytest.mark.parametrize(
    ("changed", "meta_updates", "expected"),
    [
        ([SimpleNamespace(poi_id="H1", title="如家酒店·neo(广州芳村其他店)")], {},
         "named_map_identity_not_found"),
        (None, {"address": "荔湾区东漖北路566号"}, "named_map_identity_not_found"),
        (None, {"address": "荔湾区东漖北路566568号"}, "named_map_identity_not_found"),
        ([SimpleNamespace(poi_id="H1", title="如家酒店neo(广州芳村花地湾地铁站店)")], {},
         "named_map_identity_not_found"),
        (None, {"city": "佛山市"}, "named_map_identity_not_found"),
        (None, {"type": "购物服务;商场", "typecode": "060100"},
         "named_map_identity_not_found"),
    ],
)
def test_wrong_map_name_address_city_or_category_never_calls_supplier(
    tmp_path, changed, meta_updates, expected
):
    maps = Maps(hotels=changed)
    maps.metadata["H1"].update(meta_updates)
    svc, maps, quotes, session = setup(tmp_path, maps=maps)
    response = svc.search(request(), session)
    assert response.hotels == []
    assert len(quotes.arguments) == 0
    assert response.recall_coverage.hotel_lookups_attempted == 0
    assert response.branches[0].hotel_targets[0].reason == expected
    assert response.walk_check_available is False
    assert len(maps.calls) == 2


def test_duplicate_map_hotel_is_ambiguous_and_never_sources(tmp_path):
    maps = Maps(hotels=[
        SimpleNamespace(poi_id="H1", title="如家酒店·neo(广州芳村花地湾地铁站店)"),
        SimpleNamespace(poi_id="H2", title="如家酒店·neo(广州芳村花地湾地铁站店)"),
    ])
    svc, _, quotes, session = setup(tmp_path, maps=maps)
    response = svc.search(request(), session)
    assert response.hotels == []
    assert response.branches[0].hotel_targets[0].reason == "named_map_ambiguous"
    assert len(quotes.arguments) == 0


def test_missing_map_hotel_does_not_silently_fallback_to_supplier(tmp_path):
    maps = Maps(hotels=[])
    svc, _, quotes, session = setup(tmp_path, maps=maps)
    response = svc.search(request(), session)
    assert response.status == "complete" and response.hotels == []
    assert response.recall_coverage.named_hotel_map_matches == 0
    assert len(quotes.arguments) == 0


def test_wrong_restaurant_address_prevents_map_hotel_search_and_supplier(tmp_path):
    maps = Maps()
    maps.metadata["R1"]["address"] = "天河区体育西路168号"
    svc, maps, quotes, session = setup(tmp_path, maps=maps)
    response = svc.search(request(), session)
    assert response.hotels == []
    assert [call[0] for call in maps.calls] == [RESTAURANT]
    assert len(quotes.arguments) == 0
    assert response.branches[0].reason == "named_restaurant_identity_mismatch"


def test_duplicated_exact_restaurant_must_not_choose_first(tmp_path):
    maps = Maps(restaurant=[
        SimpleNamespace(poi_id="R1", title="海底捞火锅(荔胜广场店)"),
        SimpleNamespace(poi_id="R2", title="海底捞火锅(荔胜广场店)"),
    ])
    maps.metadata["R2"] = maps.metadata["R1"].copy()
    svc, maps, quotes, session = setup(tmp_path, maps=maps)
    response = svc.search(request(), session)
    assert response.hotels == []
    assert [call[0] for call in maps.calls] == [RESTAURANT]
    assert len(quotes.arguments) == 0
    assert all(b.reason == "named_restaurant_not_unique" for b in response.branches)


@pytest.mark.parametrize("provider_name", ["other_operator", "same_name_other_door", "two_matches"])
def test_supplier_wrong_operator_door_or_ambiguous_never_becomes_member(tmp_path, provider_name):
    original = hotel(120, name=HOME, address=HOME_ADDRESS)
    source = {
        "other_operator": [hotel(120, name="隔壁公寓", address=HOME_ADDRESS)],
        "same_name_other_door": [hotel(120, name=HOME, address="东漖北路569号")],
        "two_matches": [original, hotel(121, name=HOME, address=HOME_ADDRESS)],
    }[provider_name]
    svc, _, quotes, session = setup(tmp_path, output=source)
    result = svc.search(request(), session)
    assert result.hotels == []
    assert len(quotes.arguments) == 1
    assert result.branches[0].hotel_targets[0].status in ("excluded", "ambiguous")
    assert result.walk_check_available is False


def test_first_map_error_or_supplier_error_stops_without_fallback(tmp_path):
    class FailMap(Maps):
        def search(self, keyword, city, page, page_size):
            if keyword == HOME:
                raise RuntimeError("synthetic map failure")
            return super().search(keyword, city, page, page_size)

    svc, _, quotes, session = setup(tmp_path / "map", maps=FailMap())
    result = svc.search(request(), session)
    assert result.hotels == [] and result.status == "unavailable"
    assert len(quotes.arguments) == 0

    svc, _, quotes, session = setup(tmp_path / "supplier", output=RollingGoError("ACCESS_DENIED"))
    result = svc.search(request(), session)
    assert result.hotels == [] and result.status == "unavailable"
    assert len(quotes.arguments) == 1
    assert result.walk_check_available is False


def test_cancel_during_named_map_recall_never_commits_source_or_hotel(tmp_path):
    maps = Maps()
    svc, _, quotes, session = setup(tmp_path, maps=maps)
    original = maps.search
    operation_id = uuid4().hex

    def cancelling(keyword, city, page, page_size):
        result = original(keyword, city, page, page_size)
        if keyword == HOME:
            svc.cancel(operation_id)
        return result

    maps.search = cancelling
    req = request(operation_id=operation_id)
    response = svc.search(req, session)
    assert response.status == "unavailable"
    assert response.hotels == []
    assert response.error.code == "CANCELLED"
    assert response.walk_check_available is False
    assert not quotes.arguments


@pytest.mark.parametrize("invalid", [
    {"strategy": "named_hotel", "named_hotel": None},
    {"strategy": "named_hotel", "restaurant_address": None},
    {"strategy": "map_hotels", "named_hotel": NamedHotel(name=HOME, address=HOME_ADDRESS)},
    {"strategy": "branch_place", "restaurant_address": RESTAURANT_ADDRESS},
    {"strategy": "named_hotel", "named_hotel": {"name": "短", "address": HOME_ADDRESS}},
    {"strategy": "named_hotel", "named_hotel": {"name": HOME, "address": "街"}},
    {"strategy": "named_hotel", "named_hotel": {"name": HOME[:4] + "\n" + HOME[4:],
                                               "address": HOME_ADDRESS}},
    {"strategy": "named_hotel", "restaurant_address": "花地大道\n中66号"},
])
def test_required_full_identity_validated_before_any_network(invalid):
    with pytest.raises(ValidationError):
        request(**invalid)


def test_production_fastapi_rejects_incomplete_named_request_before_source_http(tmp_path):
    svc, maps, quotes, session = setup(tmp_path)
    app = create_app(
        recall_service=svc, service=QuoteService(), session_store=session,
        web_root=tmp_path,
    )
    body = request().model_dump(mode="json")
    body["named_hotel"]["address"] = ""
    with TestClient(app, base_url="http://127.0.0.1") as client:
        reply = client.post("/api/recall", json=body)
    assert reply.status_code == 422
    assert maps.calls == []
    assert len(quotes.arguments) == 0


def test_named_success_creates_only_one_strict_restaurant_hotel_pair(tmp_path):
    svc, _, _, session = setup(tmp_path)
    res = svc.search(request(), session)
    map_walk = WalkMap()
    walk = BranchWalkService(
        svc, session, map_factory=lambda provider, deadline, cancelled: map_walk,
        data_mode="test",
    )
    record, pairs = walk.pairs(res.request_id)
    assert record["provider"] == "amap"
    assert len(pairs) == 1
    branch, named = pairs[0]
    assert branch["role"] == "restaurant_body"
    assert named["id"] == 427406
    assert branch["hotel_targets"][427406]["source_id"] == "H1"


def test_tencent_named_recall_requires_tencent_category_and_preserves_identity(tmp_path):
    maps = Maps(category="酒店:经济型酒店")
    maps.metadata["H1"]["category"] = "酒店:经济型酒店"
    maps.metadata["R1"]["category"] = "美食:火锅"
    svc, maps, quotes, session = setup(tmp_path, maps=maps)
    response = svc.search(request(provider="tencent"), session)
    assert response.status == "complete"
    assert [hotel.name for hotel in response.hotels] == [HOME]
    assert response.walk_check_available is True
    assert len(maps.calls) == 2
    assert len(quotes.arguments) == 1


def test_named_association_requires_confirmed_source_city_on_fresh_route(tmp_path):
    svc, _, _, session = setup(tmp_path)
    res = svc.search(request(), session)

    class NamedRouteMap:
        def __init__(self):
            self.metadata = {
                "H1": {"address": "荔湾区东漖北路566-568号", "city": "广州市"},
                "R1": {"address": RESTAURANT_ADDRESS, "city": "广州市"},
            }
            self.http = SimpleNamespace(request_count=0, deadline=None)
            self.walk_calls = []

        def search(self, keyword, city, page_size):
            self.http.request_count += 1
            if "如家" in keyword:
                return [SimpleNamespace(poi_id="H1", title="如家酒店·neo(广州芳村花地湾地铁站店)")]
            return [SimpleNamespace(poi_id="R1", title="海底捞火锅(荔胜广场店)")]

        def walk(self, origin, destination):
            self.http.request_count += 1
            self.walk_calls.append((origin.poi_id, destination.poi_id))
            return [{"distance_meters": 1142, "duration_seconds": 914}]

        def drive(self, *args):
            raise AssertionError("WALK_DISTANCE_ALREADY_PASS_NO_DRIVE")

    from stay_agent.contracts import BranchWalkBatchRequest, BranchWalkRouteRequest
    from stay_agent.branch_walk import BranchWalkError

    live_map = NamedRouteMap()
    walk = BranchWalkService(
        svc, session, map_factory=lambda provider, deadline, cancelled: live_map,
        data_mode="test",
    )
    batched = walk.batch(
        BranchWalkBatchRequest(request_id=res.request_id, operation_id=uuid4().hex)
    )
    assert batched.status == "partial"
    assert batched.map_http_requests == 2
    pair = batched.pairs[0]
    assert pair.status == "needs_confirmation"
    assert pair.hotel_matching.status == "unverified"
    assert pair.walk is None
    req = BranchWalkRouteRequest(
        request_id=res.request_id, context_id=pair.context_id,
        hotel_option_id=pair.hotels[0].id,
        restaurant_option_id=pair.restaurants[0].id,
    )
    with pytest.raises(BranchWalkError, match="WALK_IDENTITY_REQUIRED"):
        walk.route(req)
    verified = walk.route(req.model_copy(update={"confirm_hotel": True}))
    assert verified.status == "route_verified"
    assert verified.hotel_match_basis == "user_confirmed"
    assert verified.restaurant_match_basis == "exact_fields"
    assert verified.transport_status == "pass_walk_or_drive"
    assert verified.walk.distance_meters == 1142
    assert verified.walk_limit_status == "within_1500"
    assert live_map.walk_calls == [("R1", "H1")]
    assert live_map.http.request_count == 3
