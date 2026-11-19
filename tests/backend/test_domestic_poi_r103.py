"""R103: mainland POI relevance, official map rating, and strict map evidence."""
from stay_agent.contracts import NearbyResolveRequest, NearbyExploreRequest
from stay_agent.poi_nearby import NearbyPlaceService, _map_rating
from stay_agent.providers.data.travel_clients import MapProbe, Point


def make_point(id_, name, lng=116.40):
    return Point("amap", id_, name, lng, 39.90)


class MapScene:
    def __init__(self):
        self.points = [
            make_point("related", "故宫附近停车场"),
            make_point("wrong", "故宫", 121.0),
            make_point("missing", "故宫东门"),
            make_point("exact", "故宫"),
            make_point("same", "故宫"),
            make_point("unrelated", "王府井"),
        ]
        self.metadata = {
            "related": {"city": "北京市", "address": "北京停车场"},
            "wrong": {"city": "上海市", "address": "外滩"},
            "missing": {"address": "城市未返回"},
            "exact": {"city": "北京市", "address": "景山前街4号"},
            "same": {"city": "北京市", "address": "景山前街4号"},
            "unrelated": {"city": "北京市", "address": "王府井大街"},
        }
        self.requests = []

    def search(self, keyword, city, page=1, page_size=5):
        self.requests.append((keyword, city, page, page_size))
        return self.points

    def around(self, point, keyword, limit):
        return []


def test_resolve_enforces_returned_city_and_does_not_auto_pick():
    scene = MapScene()
    service = NearbyPlaceService(factories={"amap": lambda: scene}, data_mode="test")
    result = service.resolve(NearbyResolveRequest(keyword="故宫", city="北京", provider="amap"))
    assert result["status"] == "complete"
    assert scene.requests == [("故宫", "北京", 1, 10)]
    assert [x["name"] for x in result["options"]][:2] == ["故宫", "故宫"]
    assert not any(x["address"] == "外滩" for x in result["options"])
    assert result["context_id"] and len(service.contexts[result["context_id"]].anchors) <= 5
    assert all("poi_id" not in x and "lng" not in x for x in result["options"])


def test_duplicate_provider_point_is_suppressed():
    scene = MapScene()
    scene.points.insert(0, scene.points[3])
    service = NearbyPlaceService(factories={"amap": lambda: scene}, data_mode="test")
    result = service.resolve(NearbyResolveRequest(keyword="故宫", city="北京", provider="amap"))
    assert sum(row["address"] == "景山前街4号" for row in result["options"]) == 2
    assert len({p.poi_id for p in service.contexts[result["context_id"]].anchors.values()}) == len(result["options"])


def test_map_rating_requires_valid_five_point_numeric_evidence():
    for bad in (None, "", "[]", "4.9/5", "-1", "0", "5.1", "NaN", "Infinity", [], {}, True):
        assert _map_rating(bad) is None
    assert _map_rating("4.6") == 4.6
    assert _map_rating(5) == 5.0


def test_amap_metadata_exposes_only_actual_map_business_rating():
    probe = MapProbe("amap", "placeholder-not-used", http=object())
    points = probe._remember([{
        "id": "a", "name": "示例酒店", "location": "116.40,39.90",
        "cityname": "北京市", "business": {"rating": "4.4", "cost": "500"},
    }])
    assert len(points) == 1
    assert probe.metadata["a"]["map_business_rating"] == "4.4"
    assert "review_count" not in probe.metadata["a"]


def test_tencent_source_radius_is_strictly_one_kilometre():
    anchor = Point("tencent", "anchor", "深圳市民中心", 114.06, 22.54)
    close = Point("tencent", "near", "附近酒店", 114.063, 22.54)
    far = Point("tencent", "far", "较远酒店", 114.0705, 22.54)
    class Tencent:
        metadata = {
            "anchor": {"city": "深圳市", "address": "市民中心"},
            "near": {"city": "深圳市", "address": "福田区近路1号"},
            "far": {"city": "深圳市", "address": "福田区远路1号"},
        }
        def search(self, keyword, city, page=1, page_size=5):
            return [anchor]
        def around(self, point, keyword, limit):
            return [close, far]
    service = NearbyPlaceService(factories={"tencent": Tencent}, data_mode="test")
    resolved = service.resolve(NearbyResolveRequest(keyword="深圳市民中心", city="深圳", provider="tencent"))
    result = service.explore(NearbyExploreRequest(
        context_id=resolved["context_id"], anchor_id=resolved["options"][0]["id"],
    ), object())
    assert [h["name"] for h in result["hotels"]] == ["附近酒店"]
    assert result["hotels"][0]["rating_status"] == "unavailable"
    assert result["hotels"][0]["review_count"] is None


def test_source_rating_keeps_reviews_and_stays_unknown():
    anchor = make_point("anchor", "北京站")
    hotel = make_point("hotel", "示例酒店", 116.401)
    class Amap:
        metadata = {
            "anchor": {"city": "北京市", "address": "北京站"},
            "hotel": {"city": "北京市", "address": "建国门街道1号", "map_business_rating": "4.7"},
        }
        def search(self, keyword, city, page=1, page_size=5):
            return [anchor]
        def around(self, point, keyword, limit):
            return [hotel]
    service = NearbyPlaceService(factories={"amap": Amap}, data_mode="test")
    resolved = service.resolve(NearbyResolveRequest(keyword="北京站", city="北京"))
    response = service.explore(NearbyExploreRequest(
        context_id=resolved["context_id"], anchor_id=resolved["options"][0]["id"],
    ), object())
    assert response["status"] == "complete"
    assert response["hotels"][0]["map_rating"] == 4.7
    assert response["hotels"][0]["rating_status"] == "map_poi"
    assert response["hotels"][0]["review_count"] is None
    assert response["hotels"][0]["stayed_count"] is None
