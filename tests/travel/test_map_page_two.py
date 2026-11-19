"""Amap page 2 bounded nearby lookup: no real HTTP."""

import pytest

from stay_agent.providers.data.travel_clients import MapProbe, Point, ProbeError


class FakeHTTP:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []

    def json(self, operation, params):
        self.calls.append((operation, dict(params)))
        if self.provider == "amap":
            return {"status": "1", "pois": []}
        return {"status": 0, "data": []}


def test_amap_around_page_2_is_not_repeating_page_1():
    fake = FakeHTTP("amap")
    maps = MapProbe("amap", "fixture-map-key", http=fake)
    restaurant = Point("amap", "poi-id", "测试餐厅", 113.0, 23.0)
    maps.points[restaurant.poi_id] = restaurant
    assert maps.around(restaurant, "酒店", limit=10, page=2) == []
    assert len(fake.calls) == 1
    operation, params = fake.calls[0]
    assert operation == "around"
    assert params["page_num"] == 2
    assert params["page_size"] == 10
    assert params["radius"] == 2000
    assert params["sortrule"] == "distance"


def test_default_around_page_remains_one_and_page_input_rejected():
    fake = FakeHTTP("amap")
    maps = MapProbe("amap", "fixture-map-key", http=fake)
    restaurant = Point("amap", "poi-id", "测试餐厅", 113.0, 23.0)
    maps.points[restaurant.poi_id] = restaurant
    maps.around(restaurant, "酒店", limit=10)
    assert fake.calls[0][1]["page_num"] == 1
    for invalid in (True, 0, 3, 21, -1, "2", 1.5):
        with pytest.raises(ProbeError, match="INVALID_ARGUMENTS"):
            maps.around(restaurant, "酒店", limit=10, page=invalid)
    assert len(fake.calls) == 1


def test_tencent_page2_not_silently_rewritten_to_page1():
    fake = FakeHTTP("tencent")
    maps = MapProbe("tencent", "fixture-map-key", http=fake)
    point = Point("tencent", "poi-id", "测试餐厅", 113.0, 23.0)
    maps.points[point.poi_id] = point
    with pytest.raises(ProbeError, match="OPERATION_DENIED"):
        maps.around(point, "酒店", limit=10, page=2)
    assert fake.calls == []
