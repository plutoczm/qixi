"""Synthetic, offline source observation; DTO absence is never raw evidence."""
import pytest

from stay_agent.city_observation import CityObservationRegistry
from stay_agent.storage import SessionStore


QUERY = {"place": "上海", "place_type": "城市", "poi_name": "测试目的地"}
HOTEL = {"id": 7, "name": "测试酒店(一店)", "address": "繁荣路12号"}
RAW = {"hotelId": 7, "name": HOTEL["name"], "address": HOTEL["address"], "areaCode": "CN"}


@pytest.fixture
def observation(tmp_path):
    now = [1700000000.0]
    store = SessionStore(tmp_path / "isolated.sqlite3", ttl_seconds=30, clock=lambda: now[0])
    registry = CityObservationRegistry()
    def save(token="one", raw=None):
        store.save(token, QUERY, [7], [HOTEL], supplier_source="rollinggo")
        registry.observe(token, QUERY, [RAW] if raw is None else raw, [HOTEL], store, "observed-time")
        query, hotels = store.list_hotels(token)
        return registry.lookup(token, query, hotels, store)
    return registry, store, now, save


@pytest.mark.parametrize("fields,expected", [
    ({}, "missing"), ({"city": None}, "missing"), ({"cityName": " \t"}, "missing"),
    ({"city": []}, "present_unverified"), ({"cityCode": {}}, "present_unverified"),
    ({"city": False}, "present_unverified"), ({"city": 0}, "present_unverified"),
    ({"cityName": "上海"}, "present_unverified"), ({"cityCode": "opaque"}, "present_unverified"),
    ({"province": "unknown"}, "present_unverified"),
])
def test_only_observed_empty_fields_are_missing(observation, fields, expected):
    _, _, _, save = observation
    snapshot = save(raw=[RAW | fields])
    assert snapshot.observations[7].city_status == expected
    assert snapshot.observations[7].observed_at == "observed-time"


@pytest.mark.parametrize("fields,supported", [
    ({}, True), ({"areaCode": None}, False), ({"areaCode": ""}, False),
    ({"areaCode": []}, False), ({"areaCode": "HK"}, False),
    ({"countryCode": "US"}, False), ({"country": False}, False),
    ({"countryName": "中国"}, True),
])
def test_country_requires_supported_positive_evidence(observation, fields, supported):
    _, _, _, save = observation
    assert save(raw=[RAW | fields]).observations[7].country_supported is supported


def test_complete_raw_response_conflict_survives_projection_dedup(observation):
    _, _, _, save = observation
    snapshot = save(raw=[RAW, RAW | {"name": "同ID另一分店"}])
    assert snapshot.observations[7].identity_conflict
    snapshot = save("identical", [RAW, RAW.copy()])
    assert not snapshot.observations[7].identity_conflict
    snapshot = save("city-conflict", [RAW, RAW | {"cityName": "北京"}])
    assert snapshot.observations[7].city_status != "missing"


def test_changed_membership_query_store_and_expiry_invalidate(observation, tmp_path):
    registry, store, now, save = observation
    save()
    query, hotels = store.list_hotels("one")
    assert registry.lookup("one", query | {"poi_name": "其他"}, hotels, store) is None
    assert registry.lookup("one", query, hotels, SessionStore(tmp_path / "other.db")) is None
    store.replace_membership("one", [7], [HOTEL | {"address": "另一地址"}])
    changed_query, changed = store.list_hotels("one")
    assert registry.lookup("one", changed_query, changed, store) is None
    now[0] += 30
    assert registry.lookup("one", query, hotels, store) is None


def test_unobserved_and_eviction_never_reconstruct_raw_from_sql(observation):
    registry, store, _, save = observation
    save()
    query, hotels = store.list_hotels("one")
    assert CityObservationRegistry().lookup("one", query, hotels, store) is None
    for index in range(64):
        save(str(index))
    assert registry.lookup("one", query, hotels, store) is None
    assert registry.lookup("63", query, hotels, store) is not None


def test_search_observes_same_response_without_extra_call(tmp_path):
    from datetime import timedelta
    from stay_agent.contracts import SearchInput, china_today
    from stay_agent.service import QuoteService
    class Source:
        calls = 0
        def call_tool(self, name, arguments):
            self.calls += 1
            return {"structuredContent": {"success": True, "hotelInformationList": [RAW]}}
        def redact(self, value):
            return value
    source = Source()
    service = QuoteService(client_factory=lambda: source)
    store = SessionStore(tmp_path / "search.db")
    query = SearchInput(place="上海", place_type="城市", poi_name="测试目的地",
                        check_in=china_today() + timedelta(days=1),
                        check_out=china_today() + timedelta(days=2), adults=2, rooms=1)
    response = service.search(query, store)
    context, hotels = store.list_hotels(response.request_id)
    snapshot = service.city_observations.lookup(response.request_id, context, hotels, store)
    assert snapshot.observations[7].city_status == "missing"
    assert source.calls == 1
    assert "areaCode" not in str(snapshot) and "destinationId" not in str(snapshot)


def test_uncommitted_recall_search_needs_no_city_observation():
    from datetime import timedelta
    from stay_agent.contracts import SearchInput, china_today
    from stay_agent.map_recall import NoSearchStore
    from stay_agent.service import QuoteService
    class Source:
        def call_tool(self, name, arguments):
            return {"structuredContent": {"success": True, "hotelInformationList": [RAW]}}
        def redact(self, value):
            return value
    service = QuoteService(client_factory=Source)
    query = SearchInput(place=HOTEL["name"], place_type="酒店",
                        check_in=china_today() + timedelta(days=1),
                        check_out=china_today() + timedelta(days=2), adults=2, rooms=1)
    response = service.search(query, NoSearchStore())
    assert response.status == "complete" and response.hotels[0].id == 7
