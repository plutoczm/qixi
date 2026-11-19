"""Offline regressions for P4 city grounding and provider search context.

No real map, supplier, model or transaction calls.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from stay_agent.branch_walk import BranchWalkError, BranchWalkService
from stay_agent.contracts import BranchWalkBatchRequest, BranchWalkRouteRequest
from stay_agent.map_recall import NoSearchStore
from stay_agent.service import QuoteService
from test_branch_walk import Map as FreshMap
from test_map_recall import setup
from test_recall import Quotes, hotel, query, request


def one(tmp_path, source_hotel):
    point = SimpleNamespace(poi_id="H10", title="测试酒店10")
    svc, maps, source, store = setup(
        tmp_path, 1, {1: [point]}, [[source_hotel]]
    )
    result = svc.search(request(strategy="map_hotels"), store)
    return svc, maps, source, store, result, result.branches[0].hotel_targets[0]


def test_provider_hotel_search_is_still_an_exact_name_with_grounded_context(tmp_path):
    _, _, source, _, result, target = one(tmp_path, hotel(10))
    assert len(source.arguments) == 1
    arguments = source.arguments[0]
    assert arguments["place"] == "测试酒店10"
    assert arguments["placeType"] == "酒店"
    assert "地图候选城市：广州" in arguments["originQuery"]
    assert "地图候选地址：测试街10号" in arguments["originQuery"]
    assert arguments["hotelTags"]["maxPricePerNight"] == 200
    assert arguments["checkInParam"]["adultCount"] == 2
    assert arguments["size"] == result.context.limit
    assert target.status == "retained"
    # Name/address identity does not prove the supplier is in Guangzhou.
    assert target.match_status == "unverified"
    assert target.reason == "source_city_unverified"


def test_original_non_map_provider_query_remains_unchanged():
    source = Quotes([[hotel(7)]])
    service = QuoteService()
    response = service.search(query(), NoSearchStore(), client=source)
    assert response.status == "complete"
    arguments = source.arguments[0]
    assert arguments["place"] == "广州 中国"
    assert "地图候选城市：" not in arguments["originQuery"]
    assert "地图候选地址：" not in arguments["originQuery"]


def test_bounded_search_hint_rejected_before_any_http():
    source = Quotes([[hotel(7)]])
    with pytest.raises(ValueError, match="bounded map context"):
        QuoteService().search(query(), NoSearchStore(), client=source,
                              origin_hint="X" * 551)
    assert source.request_count == 0


def test_foreign_same_name_is_unverified_with_explicit_address_warning(tmp_path):
    _, _, source, store, result, target = one(
        tmp_path, hotel(10, address="Ho Chi Minh City, Vietnam")
    )
    assert target.status == "retained"
    assert target.reason == "source_address_mismatch"
    assert target.match_status == "unverified"
    assert store.hotel(result.request_id, 10)["address"] == "Ho Chi Minh City, Vietnam"
    assert len(source.arguments) == 1


def test_same_address_different_name_requires_identity_review(tmp_path):
    _, _, _, _, _, target = one(
        tmp_path, hotel(10, name="另一个公寓", address="测试街10号")
    )
    assert target.status == "retained"
    assert target.match_status == "unverified"
    assert target.reason == "source_name_mismatch"


def test_map_first_walk_does_not_auto_use_requested_city_as_source_city(tmp_path):
    svc, _, _, store, result, target = one(tmp_path, hotel(10))
    assert target.reason == "source_city_unverified"
    map_client = FreshMap()
    walk = BranchWalkService(
        svc, store, map_factory=lambda *_: map_client, data_mode="test"
    )
    batch = walk.batch(BranchWalkBatchRequest(
        request_id=result.request_id, operation_id=uuid4().hex
    ))
    pair = batch.pairs[0]
    assert pair.context_id
    assert pair.hotel_matching.status == "unverified"
    assert pair.restaurant_matching.status == "matched"
    assert pair.walk is None
    assert not map_client.walks
    route_input = BranchWalkRouteRequest(
        request_id=result.request_id,
        context_id=pair.context_id,
        hotel_option_id=pair.hotels[0].id,
        restaurant_option_id=pair.restaurants[0].id,
    )
    with pytest.raises(BranchWalkError, match="WALK_IDENTITY_REQUIRED"):
        walk.route(route_input)
    assert not map_client.walks
    verified = walk.route(route_input.model_copy(update={"confirm_hotel": True}))
    assert verified.status == "route_verified"
    assert verified.hotel_match_basis == "user_confirmed"
    assert verified.restaurant_match_basis == "exact_fields"
    assert map_client.walks == [("测试餐厅1店", "测试酒店10")]
