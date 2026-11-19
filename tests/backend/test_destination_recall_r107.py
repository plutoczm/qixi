"""Destination narrowing does not relax independent city/name/address identity."""
from datetime import date, timedelta
from stay_agent.contracts import SearchInput
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore
from stay_agent.poi_nearby import _join_supplier_matches
from tests.backend.test_price_web import FakeClient


def test_destination_hint_uses_documented_poi_radius_and_keeps_query_context(tmp_path):
    client = FakeClient()
    query = SearchInput(place="广州", poi_name="荔胜广场", check_in=date.today()+timedelta(days=3),
                        check_out=date.today()+timedelta(days=4), adults=3, rooms=2, children_ages=[6], budget_fen=30000)
    store = SessionStore(tmp_path / "sessions.sqlite")
    result = QuoteService(lambda: client).search(query, store)
    args = client.calls[0][1]
    assert args["place"] == "荔胜广场 广州"
    assert args["placeType"] == "详细地址"
    assert args["filterOptions"] == {"distanceInMeter": 2000}
    assert args["hotelTags"]["maxPricePerNight"] == 300
    assert result.context == query
    assert store.load(result.request_id, 17)["children_ages"] == [6]
    assert len(client.calls) == 1


def row():
    return {"id": "map-token", "name": "同名酒店", "address": "上海市浦东新区繁荣路12号",
            "source_hotel_id": None, "source_match": "unverified"}


def test_poi_supplier_result_requires_own_explicit_city_not_map_city():
    hotel = row()
    _join_supplier_matches([hotel], [{"id": 17, "name": "同名酒店", "address": "浦东新区繁荣路12号"}],
                           {"place": "上海", "place_type": "城市", "poi_name": "迪士尼"}, "上海")
    assert hotel["source_hotel_id"] is None


def test_poi_supplier_explicit_city_and_unique_identity_can_join():
    hotel = row()
    _join_supplier_matches([hotel], [{"id": 17, "name": "同名酒店", "address": "上海市浦东新区繁荣路12号"}],
                           {"place": "上海", "place_type": "城市", "poi_name": "迪士尼"}, "上海")
    assert hotel["source_hotel_id"] == 17
