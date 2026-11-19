"""P2 conservative roles: synthetic evidence only; zero provider calls."""
import pytest
from stay_agent.restaurant_roles import classify_restaurant
from test_recall import points, hotel, setup, request

@pytest.mark.parametrize("provider,meta,expected", [
    ("amap", {"typecode": "050117", "type": "餐饮服务;中餐厅"}, "restaurant_body"),
    ("tencent", {"category": "美食:火锅"}, "restaurant_body"),
    ("amap", {}, "unknown"),
    ("tencent", {"category": "生活服务:充电设备"}, "unknown"),
    ("amap", {"typecode": "070000"}, "unknown"),
])
def test_evidence_needed_for_dining(provider, meta, expected):
    result = classify_restaurant(provider, "海底捞火锅北京路店", meta)
    assert result.role == expected

@pytest.mark.parametrize("provider,meta", [
    ("amap", {"typecode": "050117"}),
    ("tencent", {"category": "美食:火锅"}),
])
def test_name_conflict_is_never_positive(provider, meta):
    assert classify_restaurant(provider, "海底捞外送北京路店", meta).role == "unknown"
    assert classify_restaurant(provider, "来电(海底捞火锅北京路店)", meta).role == "unknown"

@pytest.mark.parametrize("provider", ["amap", "tencent"])
def test_mixed_roles_only_verified_restaurant_triggers_quotes(tmp_path, provider):
    svc, maps, source, store = setup(tmp_path, [points(1, 3)], [[hotel(1)]])
    original = maps.search
    def search(*args, **kwargs):
        result = original(*args, **kwargs)
        maps.metadata["2"]["category"] = "生活服务:充电设备"
        maps.metadata["2"]["typecode"] = "070000"
        maps.metadata["3"]["category"] = None
        maps.metadata["3"]["typecode"] = None
        return result
    maps.search = search
    result = svc.search(request(provider=provider), store)
    assert result.recall_coverage.role_restaurant_bodies == 1
    assert result.recall_coverage.role_unknown == 2
    assert len(source.arguments) == 1
    assert result.branches[0].role == "restaurant_body"
    assert all(not b.hotel_ids and not b.query_place for b in result.branches[1:])
