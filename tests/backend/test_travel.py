"""Travel references use source-specific points and conservative lodging identity."""

from datetime import date
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from stay_agent.providers.data.travel_clients import Point, ProbeError
from stay_agent.contracts import SearchInput
from stay_agent.main import create_app
from stay_agent.storage import SessionStore, SessionError
from stay_agent.travel import TravelService
from stay_agent.travel_rules import assessment, fare_amount, entrance_name


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    monkeypatch.setattr("stay_agent.contracts.china_today", lambda: date(2026, 10, 16))


def test_exact_match_requires_all_fields_and_unique_candidate():
    hotel = {"name": "逸米酒店（北京路店）", "address": "万福路351号"}
    candidate = {
        "id": "one",
        "name": "逸米酒店(北京路店)",
        "address": "万福路351号",
        "city": "广州市",
    }
    _, result = assessment(hotel, [candidate], "广州", "广州")
    assert result["status"] == "matched" and result["selected_id"] == "one"
    _, result = assessment(hotel, [candidate, {**candidate, "id": "two"}], "广州", "广州")
    assert result["status"] == "ambiguous" and result["selected_id"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"address": None},
        {"address": "万福路351号201室"},
        {"name": "逸米酒店(北京路站店)"},
        {"city": "佛山市"},
    ],
)
def test_missing_or_different_branch_room_city_never_auto_matches(change):
    hotel = {"name": "逸米酒店(北京路店)", "address": "万福路351号"}
    candidate = {
        "id": "one",
        "name": hotel["name"],
        "address": hotel["address"],
        "city": "广州市",
        **change,
    }
    _, result = assessment(hotel, [candidate], "广州", "广州")
    assert result["status"] == "unverified"


def test_numeric_delimiters_and_branch_wording_are_never_erased():
    hotel = {"name": "公寓", "address": "1-2号"}
    candidate = {"id": "one", "name": "公寓", "address": "12号", "city": "广州"}
    assert assessment(hotel, [candidate], "广州", "广州")[1]["status"] == "unverified"
    candidate["address"] = "1-2号"
    # 2026-11-05 user-approved basis: the places flow knows the stay came from a
    # search issued for that city, so it may pass stay_city_basis explicitly.
    assert (
        assessment(hotel, [candidate], None, "广州", stay_city_basis="广州")[1]["status"]
        == "matched"
    )
    # Without that basis (map-point recovery) identity must stay unverified.
    assert assessment(hotel, [candidate], None, "广州")[1]["status"] == "unverified"
    # A candidate from a different city still never auto-matches.
    assert (
        assessment(
            hotel, [{**candidate, "city": "佛山市"}], None, "广州", stay_city_basis="广州"
        )[1]["status"]
        == "unverified"
    )


@pytest.mark.parametrize(
    "candidate_address,expected",
    [
        ("永乐路25号(东漖北路口)", "matched"),
        ("永乐路25号（东漖北路口）", "matched"),
        ("永乐路25号A口步行330米", "matched"),
        ("永乐路25号3楼前台", "matched"),
        ("永乐路26号(东漖北路口)", "unverified"),
        ("永乐路25号201室", "unverified"),
        ("永乐路25号之一", "unverified"),
        ("永乐路25号(" + "补充" * 25 + ")", "unverified"),
        ("", "unverified"),
        (None, "unverified"),
    ],
)
def test_address_core_ignores_only_known_map_annotations(candidate_address, expected):
    hotel = {"name": "逸米酒店(广州芳村地铁站花地店)", "address": "永乐路25号"}
    candidate = {
        "id": "one",
        "name": hotel["name"],
        "address": candidate_address,
        "city": "广州市",
    }
    result = assessment(hotel, [candidate], None, "广州", stay_city_basis="广州")[1]
    assert result["status"] == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("14", (1400, 1400)),
        ("15.01", (1501, 1501)),
        ("￥12～18元", (1200, 1800)),
        (None, (None, None)),
        (True, (None, None)),
        ("12起", (None, None)),
        ("约12元", (None, None)),
        ("15.001", (None, None)),
        ("18-12", (None, None)),
    ],
)
def test_fare_references_do_not_invent_or_use_range_lower_bound(value, expected):
    assert fare_amount(value) == expected


@pytest.mark.parametrize("name", ["林和西地铁站B口", "石牌桥-B口", "公园前地铁站出入口"])
def test_native_subway_entrance_names(name):
    assert entrance_name(name)


class FakeMap:
    def __init__(self, provider, missing_address=False, metro_failure=False):
        self.provider = provider
        self.missing_address = missing_address
        self.metro_failure = metro_failure
        self.walk_seconds = 601
        self.walk_meters = 800
        self.drive_seconds = 771
        self.drive_meters = 3500
        self.drive_failure = False
        self.points = {}
        self.metadata = {}
        self.calls = []
        self.http = SimpleNamespace(deadline=None)
        self.geocode_result = []

    def geocode(self, address, city):
        self.calls.append(("geocode", address, city))
        return list(self.geocode_result)

    def search(self, keyword, city, page=1, page_size=10):
        self.calls.append(("search", keyword, page))
        is_hotel = keyword == "测试住宿"
        values = (
            [Point(self.provider, "hotel", "测试住宿", 113, 23)]
            if is_hotel
            else [
                Point(self.provider, "r1", "海底捞甲店", 113.01, 23),
                Point(self.provider, "r2", "海底捞乙店", 113.02, 23),
            ]
        )
        for p in values:
            self.points[p.poi_id] = p
            self.metadata[p.poi_id] = {
                "address": None if self.missing_address else "测试地址",
                "city": "广州市",
                "open_today": "10:00-02:00",
                "open_week": None,
            }
        return values

    def walk(self, origin, destination):
        self.calls.append(("walk", origin.poi_id, destination.poi_id))
        return [
            {"duration_seconds": self.walk_seconds, "distance_meters": self.walk_meters},
            {"duration_seconds": 700, "distance_meters": 750},
        ]

    def drive(self, origin, destination):
        self.calls.append(("drive", origin.poi_id, destination.poi_id))
        if self.drive_failure:
            raise ProbeError("RATE_LIMITED")
        return [
            {
                "duration_seconds": self.drive_seconds,
                "distance_meters": self.drive_meters,
            }
        ]

    def around(self, point, keyword, limit=3):
        if self.metro_failure:
            raise ProbeError("RATE_LIMITED")
        p = Point(self.provider, "station", "林和西地铁站B口", 113.03, 23)
        self.points[p.poi_id] = p
        self.metadata[p.poi_id] = {"address": "测试站点", "city": "广州"}
        return [p]


class FakeDidi:
    def __init__(self):
        self.points = {}
        self.metadata = {}
        self.calls = []
        self.http = SimpleNamespace(deadline=None)

    def call_readonly(self, name, args):
        self.calls.append((name, args))
        if name == "maps_textsearch":
            hotel = args["keywords"] == "测试住宿"
            return {
                "structuredContent": [
                    {
                        "display_name": "测试住宿" if hotel else "海底捞甲店",
                        "address": "测试地址",
                        "city": "广州市",
                        "location": {"lng": 113 if hotel else 113.01, "lat": 23},
                    }
                ]
            }
        return {
            "structuredContent": {
                "traceId": "private-trace",
                "phoneNumberSuffix": "private-phone",
                "appLink": "https://invalid.test/private",
                "items": [
                    {
                        "productName": "快车",
                        "priceText": "12-18",
                        "productCategory": "private-category",
                    }
                ],
            }
        }


def environment(tmp_path, missing_address=False, metro_failure=False):
    store = SessionStore(tmp_path / "session.db")
    query = SearchInput(place="广州", check_in="2026-11-06", check_out="2026-11-07")
    store.save(
        "search",
        query.model_dump(mode="json"),
        [17],
        hotel_metadata=[{"id": 17, "name": "测试住宿", "address": "测试地址"}],
    )
    maps = {
        provider: FakeMap(provider, missing_address, metro_failure)
        for provider in ("amap", "tencent")
    }
    didi = FakeDidi()
    clock = [100.0]
    service = TravelService(
        factories={
            "amap": lambda: maps["amap"],
            "tencent": lambda: maps["tencent"],
            "didi": lambda: didi,
        },
        clock=lambda: clock[0],
        data_mode="test",
    )
    quote = SimpleNamespace(credential_configured=lambda: True)
    app = create_app(
        service=quote, session_store=store, travel_service=service, web_root=tmp_path / "no-web"
    )
    return TestClient(app, base_url="http://127.0.0.1"), maps, didi, service, clock


def resolve(client, provider="amap"):
    response = client.post(
        "/api/hotels/17/travel/places",
        json={"request_id": "search", "provider": provider, "city": "广州"},
    )
    assert response.status_code == 200
    return response.json()


def route_args(places, **changes):
    return {
        "request_id": "search",
        "context_id": places["context_id"],
        "hotel_option_id": places["hotels"][0]["id"],
        "restaurant_option_id": places["restaurants"][0]["id"],
        **changes,
    }


def test_api_exact_match_direction_and_whole_route(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    places = resolve(client)
    assert places["data_mode"] == "test" and places["matching"]["status"] == "matched"
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["status"] == "complete" and result["hotel_match_basis"] == "exact_fields"
    assert maps["amap"].calls[-2] == ("walk", "r1", "hotel")
    assert maps["amap"].calls[-1] == ("walk", "hotel", "station")
    assert result["return_walk"]["duration_seconds"] == 601
    assert result["return_walk"]["distance_meters"] == 800
    assert result["subway"]["precision"] == "named_entrance_poi"
    assert result["future_departure_verified"] is False


def test_unverified_match_requires_explicit_confirmation(tmp_path):
    client, maps, _, _, _ = environment(tmp_path, missing_address=True)
    places = resolve(client)
    count = len(maps["amap"].calls)
    denied = client.post("/api/hotels/17/travel/route", json=route_args(places))
    assert denied.status_code == 422
    assert len(maps["amap"].calls) == count
    accepted = client.post(
        "/api/hotels/17/travel/route", json=route_args(places, confirm_hotel=True)
    ).json()
    assert accepted["hotel_match_basis"] == "user_confirmed"


def test_expiry_cross_context_and_wrong_roles_do_not_query_source(tmp_path):
    client, maps, _, service, clock = environment(tmp_path)
    places = resolve(client)
    count = len(maps["amap"].calls)
    bad = route_args(places, hotel_option_id=places["restaurants"][0]["id"])
    assert client.post("/api/hotels/17/travel/route", json=bad).status_code == 422
    assert len(maps["amap"].calls) == count
    second = resolve(client, "tencent")
    bad = route_args(places, context_id=second["context_id"])
    assert client.post("/api/hotels/17/travel/route", json=bad).status_code == 422
    clock[0] = 221
    assert client.post("/api/hotels/17/travel/route", json=route_args(places)).status_code == 410
    assert (
        client.post(
            "/api/hotels/18/travel/places",
            json={"request_id": "search", "provider": "amap", "city": "广州"},
        ).status_code
        == 404
    )


def test_partial_metro_failure_retains_actual_return_route(tmp_path):
    client, _, _, _, _ = environment(tmp_path, metro_failure=True)
    places = resolve(client)
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["status"] == "partial"
    assert result["return_walk"]["duration_seconds"] == 601
    assert result["subway"] is None


def test_didi_fares_do_not_expose_trace_links_or_claim_capacity(tmp_path):
    client, _, didi, _, _ = environment(tmp_path)
    places = resolve(client, "didi")
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["fares"][0]["upper_fen"] == 1800
    assert result["fares"][0]["capacity_verified"] is False
    assert "private-" not in json.dumps(result)
    args = didi.calls[-1][1]
    assert args["from_name"] == "海底捞甲店" and args["to_name"] == "测试住宿"
    assert "passenger_phone" not in args


def test_schema1_backup_and_migration_preserve_original_context(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE search_sessions(request_id TEXT PRIMARY KEY,created_at REAL,expires_at REAL,context_json TEXT,hotel_ids_json TEXT)"
        )
        db.execute(
            "INSERT INTO search_sessions VALUES ('old',0,999999999999,?,?)",
            (json.dumps({"place": "广州"}), "[17]"),
        )
        db.execute("PRAGMA user_version=1")
    store = SessionStore(path)
    assert store.load("old", 17) == {"place": "广州"}
    with pytest.raises(SessionError, match="HOTEL_CONTEXT_MISSING"):
        store.hotel("old", 17)
    backups = list(tmp_path.glob("old.v1-backup-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM search_sessions").fetchone()[0] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_busy_context_and_global_capacity_do_not_call_source(tmp_path):
    client, maps, _, service, _ = environment(tmp_path)
    places = resolve(client)
    context = service.contexts[places["context_id"]]
    before = len(maps["amap"].calls)
    context.lock.acquire()
    try:
        result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
        assert result["error"]["code"] == "BUSY"
    finally:
        context.lock.release()
    service.capacity.acquire()
    service.capacity.acquire()
    try:
        result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
        assert result["error"]["code"] == "BUSY"
        assert not context.lock.locked()
    finally:
        service.capacity.release()
        service.capacity.release()
    assert len(maps["amap"].calls) == before


def test_explicit_route_resets_deadline_but_preserves_request_budget(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    places = resolve(client)
    http = maps["amap"].http
    http.deadline = 1
    http.request_count = 17
    response = client.post("/api/hotels/17/travel/route", json=route_args(places))
    assert response.status_code == 200 and response.json()["return_walk"]
    assert http.deadline is None and http.request_count == 17


def test_different_search_context_never_reuses_other_travel_points(tmp_path):
    client, maps, _, service, _ = environment(tmp_path)
    places = resolve(client)
    service.contexts[places["context_id"]].request_id = "different-search"
    before = len(maps["amap"].calls)
    denied = client.post("/api/hotels/17/travel/route", json=route_args(places))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "TRAVEL_CONTEXT_MISMATCH"
    assert len(maps["amap"].calls) == before


def test_walk_over_1500_queries_same_source_drive_and_passes_under_900(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    maps["amap"].walk_meters = 2008
    maps["amap"].walk_seconds = 1606
    places = resolve(client)
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["return_walk"]["distance_meters"] == 2008
    assert result["walk_limit_status"] == "over_1500"
    assert result["return_drive"]["duration_seconds"] == 771
    assert result["drive_limit_status"] == "within_900"
    assert result["transport_status"] == "pass_walk_or_drive"
    assert ("drive", "r1", "hotel") in maps["amap"].calls


def test_walk_within_1500_skips_paid_drive_request(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    maps["amap"].walk_meters = 1500
    maps["amap"].walk_seconds = 1200
    places = resolve(client)
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["walk_limit_status"] == "within_1500"
    assert result["return_drive"] is None
    assert result["drive_limit_status"] == "unverified"
    assert result["transport_status"] == "pass_walk_or_drive"
    assert not any(call[0] == "drive" for call in maps["amap"].calls)


def test_walk_and_drive_both_over_limit_is_rejected(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    maps["amap"].walk_meters = 1501
    maps["amap"].walk_seconds = 1300
    maps["amap"].drive_seconds = 901
    places = resolve(client)
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["walk_limit_status"] == "over_1500"
    assert result["drive_limit_status"] == "over_900"
    assert result["transport_status"] == "rejected_both_modes_over_limit"


def test_drive_failure_keeps_walk_result_and_unknown_transport(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    maps["amap"].walk_meters = 1600
    maps["amap"].walk_seconds = 1400
    maps["amap"].drive_failure = True
    places = resolve(client)
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["status"] == "partial"
    assert result["return_walk"]["distance_meters"] == 1600
    assert result["return_drive"] is None
    assert result["drive_limit_status"] == "unverified"
    assert result["transport_status"] == "unknown_or_partial"


@pytest.mark.parametrize("level", ["门址", "门牌号", "兴趣点"])
def test_geocode_confirms_a_single_name_and_city_candidate(tmp_path, level):
    client, maps, _, _, _ = environment(tmp_path, missing_address=True)
    maps["amap"].geocode_result = [{"level": level, "lng": 113.0005, "lat": 23.0}]
    body = resolve(client)
    assert body["matching"]["status"] == "matched"
    assert "地理编码" in body["matching"]["explanation"]
    assert [call[0] for call in maps["amap"].calls].count("geocode") == 1


def test_geocode_beyond_threshold_stays_unverified(tmp_path):
    client, maps, _, _, _ = environment(tmp_path, missing_address=True)
    maps["amap"].geocode_result = [{"level": "门牌号", "lng": 113.02, "lat": 23.0}]
    body = resolve(client)
    assert body["matching"]["status"] == "unverified"


def test_geocode_imprecise_level_is_not_identity_evidence(tmp_path):
    client, maps, _, _, _ = environment(tmp_path, missing_address=True)
    maps["amap"].geocode_result = [{"level": "区县", "lng": 113.0, "lat": 23.0}]
    body = resolve(client)
    assert body["matching"]["status"] == "unverified"


def test_geocode_is_skipped_without_a_unique_plausible_candidate():
    options = [
        {"id": "a", "name_match": True, "city_match": True, "address_match": False},
        {"id": "b", "name_match": True, "city_match": True, "address_match": False},
    ]
    refs = {
        "a": Point("amap", "a", "甲", 113, 23),
        "b": Point("amap", "b", "乙", 113.001, 23),
    }
    calls = []

    def geocode(address, city):
        calls.append((address, city))
        return [{"level": "门牌号", "lng": 113, "lat": 23}]

    matched = {"status": "unverified", "selected_id": None, "explanation": "待核实"}
    result = TravelService._geocode_identity(
        SimpleNamespace(geocode=geocode), {"address": "测试地址"}, options, refs, matched, "广州"
    )
    assert result == matched and calls == []


def test_geocode_is_skipped_when_the_stay_has_no_address():
    options = [{"id": "a", "name_match": True, "city_match": True, "address_match": False}]
    refs = {"a": Point("amap", "a", "甲", 113, 23)}
    calls = []

    def geocode(address, city):
        calls.append(address)
        return []

    matched = {"status": "unverified", "selected_id": None, "explanation": "待核实"}
    result = TravelService._geocode_identity(
        SimpleNamespace(geocode=geocode), {"address": None}, options, refs, matched, "广州"
    )
    assert result == matched and calls == []


def test_card_destination_uses_the_user_keyword_and_skips_unrequested_subway(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    response = client.post(
        "/api/hotels/17/travel/places",
        json={
            "request_id": "search", "provider": "amap", "city": "广州",
            "destination_keyword": "永庆坊",
        },
    )
    assert response.status_code == 200
    assert ("search", "永庆坊", 1) in maps["amap"].calls
    result = client.post("/api/hotels/17/travel/route", json=route_args(response.json())).json()
    assert result["return_walk"]["duration_seconds"] == 601
    assert result["subway"] is None
    assert maps["amap"].calls[-1] == ("walk", "r1", "hotel")


def test_card_destination_can_report_driving_when_source_returns_no_walk(tmp_path):
    client, maps, _, _, _ = environment(tmp_path)
    maps["amap"].walk = lambda *_: []
    places = client.post(
        "/api/hotels/17/travel/places",
        json={
            "request_id": "search", "provider": "amap", "city": "广州",
            "destination_keyword": "永庆坊",
        },
    ).json()
    result = client.post("/api/hotels/17/travel/route", json=route_args(places)).json()
    assert result["status"] == "partial"
    assert result["return_walk"] is None
    assert result["return_drive"]["duration_seconds"] == 771
    assert result["fares"] == []
