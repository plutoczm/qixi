"""Offline application acceptance using synthetic source data, never live keys."""

from datetime import date
import json
import sqlite3
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from stay_agent.providers.data.rollinggo_client import redact
from stay_agent.main import create_app
from stay_agent.projection import hotel_summary, money_fen, public_https, room_quote
from stay_agent.service import QuoteService
from stay_agent.storage import SessionError, SessionStore

SECRET = "fixture-not-a-real-secret"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("stay_agent.contracts.china_today", lambda: date(2026, 10, 16))


def query(**changes):
    result = {"place": "广州", "check_in": "2026-11-06", "check_out": "2026-11-07"}
    return result | changes


class FakeClient:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.mismatch = False

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.failure == "exception":
            raise RuntimeError(SECRET)
        if self.failure == "business":
            return {"structuredContent": {"success": False, "message": SECRET}}
        if name == "searchHotels":
            return {
                "structuredContent": {
                    "success": True,
                    "hotelInformationList": [
                        {
                            "hotelId": 17,
                            "name": "测试住宿",
                            "address": "测试地址",
                            "starRating": 5,
                            "price": {"hasPrice": True, "currency": "CNY", "lowestPrice": 88},
                            "bookingUrl": "https://rollinggo.cn/book?context=test",
                            "private_room": True,
                            "ensuite": True,
                            "secret": SECRET,
                        }
                    ],
                }
            }
        return {
            "structuredContent": {
                "success": True,
                "hotelId": 18 if self.mismatch else args["hotelId"],
                "name": "测试住宿",
                "checkIn": args["dateParam"]["checkInDate"],
                "checkOut": args["dateParam"]["checkOutDate"],
                "bookingUrl": "https://rollinggo.cn/book?"
                + urlencode(
                    {
                        "id": args["hotelId"],
                        **args["dateParam"],
                        **{
                            key: value
                            for key, value in args["occupancyParam"].items()
                            if key != "childAgeDetails"
                        },
                        "childAgeDetails": json.dumps(args["occupancyParam"]["childAgeDetails"]),
                    }
                ),
                "roomRatePlans": [
                    {
                        "ratePlanId": "synthetic-plan",
                        "roomName": "测试双床房",
                        "averagePrice": 80 + len(self.calls),
                        "currency": "CNY",
                        "cancelPolicy": "不可取消、不可退款",
                        "cancelable": False,
                        "isOnRequest": True,
                        "roomInfo": {"maxOccupancy": 2, "size": 20, "hasWifi": True},
                    }
                ],
            }
        }

    def redact(self, value):
        return redact(value, secrets=(SECRET,))


@pytest.fixture
def setup(tmp_path):
    fake = FakeClient()
    service = QuoteService(client_factory=lambda: fake)
    service.data_mode = "test"
    service.credential_configured = lambda: True
    store = SessionStore(tmp_path / "sessions.sqlite3")
    app = create_app(service=service, session_store=store, web_root=tmp_path / "web")
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    return client, fake, store


@pytest.mark.parametrize(
    "changes",
    [
        {"budget_fen": True},
        {"adults": "2"},
        {"rooms": 0},
        {"adults": 9},
        {"children_ages": [-1]},
        {"children_ages": [18]},
        {"limit": 21},
        {"check_out": "2026-11-06"},
        {"check_out": "2026-12-06"},
        {"check_in": "2020-01-01"},
        {"place": " "},
        {"data_mode": "test"},
    ],
)
def test_invalid_input_never_calls_source(setup, changes):
    client, fake, _ = setup
    response = client.post("/api/search", json=query(**changes))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert not fake.calls


@pytest.mark.parametrize(
    "value,currency,expected",
    [
        ("333.01", "CNY", 33301),
        (0, "CNY", 0),
        (True, "CNY", None),
        (-1, "CNY", None),
        ("NaN", "CNY", None),
        ("Infinity", "CNY", None),
        ("1.001", "CNY", None),
        (1, "USD", None),
        (1, None, None),
    ],
)
def test_integer_fen_without_guessed_conversion(value, currency, expected):
    assert money_fen(value, currency) == expected


def test_projection_does_not_turn_stars_or_room_text_into_evidence():
    hotel = hotel_summary(
        {
            "hotelId": 17,
            "name": "测试",
            "starRating": 5,
            "price": {"currency": "CNY", "lowestPrice": 88},
        }
    )
    assert hotel.reference_price_fen is None  # hasPrice absent
    assert hotel.guest_score is None
    assert "独立卫浴待核实" in hotel.unverified
    quote = room_quote({"roomName": "独卫房", "averagePrice": 88, "currency": "CNY"}, 0)
    assert quote.amount_fen == 8800
    assert quote.cancellation.can_cancel is None
    assert "独立卫浴待核实" in quote.unverified
    assert "stay_total_fen" not in quote.model_dump()


@pytest.mark.parametrize(
    "url",
    [
        "http://rollinggo.cn/book",
        "javascript:alert(1)",
        "https://rollinggo.cn.evil.test/",
        "https://user:password@rollinggo.cn/",
        "https://127.0.0.1/",
        "https://rollinggo.cn/[REDACTED]",
    ],
)
def test_untrusted_booking_url_is_rejected(url):
    assert public_https(url, booking=True) is None


def test_original_booking_url_is_preserved():
    url = "https://book.rollinggo.cn/path?a=one%20two&b=unchanged#room"
    assert public_https(url, booking=True) == url


def test_search_details_binding_and_refresh(setup):
    client, fake, store = setup
    original = query(adults=3, rooms=2, children_ages=[5], budget_fen=20000)
    result = client.post("/api/search", json=original).json()
    assert result["data_mode"] == "test"
    assert result["hotels"][0]["guest_score"] is None
    request_id = result["request_id"]
    first = client.post("/api/hotels/17/quotes", json={"request_id": request_id}).json()
    second = client.post("/api/hotels/17/quotes", json={"request_id": request_id}).json()
    assert first["quotes"][0]["amount_fen"] != second["quotes"][0]["amount_fen"]
    assert first["quotes"][0]["on_request"] is True
    assert first["quotes"][0]["cancellation"]["can_cancel"] is False
    detail = fake.calls[-1][1]
    assert detail["occupancyParam"] == {
        "adultCount": 3,
        "roomCount": 2,
        "childCount": 1,
        "childAgeDetails": [5],
    }
    assert detail["dateParam"] == {"checkInDate": "2026-11-06", "checkOutDate": "2026-11-07"}
    assert "countryCode" not in fake.calls[0][1]
    assert "localeParam" not in detail
    with sqlite3.connect(store.path) as db:
        record = db.execute("SELECT context_json, hotel_ids_json FROM search_sessions").fetchone()
        assert "averagePrice" not in str(record)
        assert "bookingUrl" not in str(record)
        assert SECRET not in str(record)
    assert SECRET not in json.dumps(result | second)


def test_foreign_or_expired_search_never_queries_details(setup, tmp_path):
    client, fake, _ = setup
    request_id = client.post("/api/search", json=query()).json()["request_id"]
    count = len(fake.calls)
    assert client.post("/api/hotels/18/quotes", json={"request_id": request_id}).status_code == 404
    assert client.post("/api/hotels/17/quotes", json={"request_id": "absent"}).status_code == 404
    assert len(fake.calls) == count
    clock = [100.0]
    store = SessionStore(tmp_path / "expiry.db", ttl_seconds=10, clock=lambda: clock[0])
    store.save("one", query(), [17])
    clock[0] = 110.0
    with pytest.raises(SessionError, match="SEARCH_EXPIRED"):
        store.load("one", 17)


def test_midnight_invalidates_old_checkin_without_provider_call(setup, monkeypatch):
    client, fake, _ = setup
    request_id = client.post("/api/search", json=query()).json()["request_id"]
    count = len(fake.calls)
    monkeypatch.setattr("stay_agent.contracts.china_today", lambda: date(2026, 11, 7))
    assert client.post("/api/hotels/17/quotes", json={"request_id": request_id}).status_code == 410
    assert len(fake.calls) == count


def test_business_failure_and_context_mismatch_are_honest(setup):
    client, fake, _ = setup
    fake.failure = "business"
    response = client.post("/api/search", json=query())
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert not response.json()["hotels"]
    assert SECRET not in response.text
    fake.failure = None
    request_id = client.post("/api/search", json=query()).json()["request_id"]
    fake.mismatch = True
    result = client.post("/api/hotels/17/quotes", json={"request_id": request_id}).json()
    assert result["status"] == "unavailable"
    assert result["error"]["code"] == "CONTEXT_MISMATCH"
    assert not result["quotes"]


def test_health_validation_and_exceptions_never_leak_secrets(setup, caplog):
    client, fake, _ = setup
    health = client.get("/api/health")
    assert health.json()["features"] == {"maps": True, "fare": True, "llm": True,
                                         "multi_branch_recall": True}
    assert not fake.calls
    invalid = client.post("/api/search", json=query(unknown=SECRET))
    assert invalid.status_code == 422
    assert SECRET not in invalid.text
    fake.failure = "exception"
    failure = client.post("/api/search", json=query())
    assert failure.status_code == 500
    assert SECRET not in failure.text + caplog.text


def test_origin_host_body_limits_and_no_order_routes(setup):
    client, fake, _ = setup
    assert (
        client.post(
            "/api/search", json=query(), headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/search", content="x" * 17000).status_code == 413
    assert client.post("/api/booking", json={}).status_code == 404
    assert not fake.calls


def test_unexpected_error_is_contained_before_server_traceback(setup, caplog):
    client, fake, _ = setup
    fake.failure = "exception"
    strict_client = TestClient(client.app, base_url="http://127.0.0.1")
    result = strict_client.post("/api/search", json=query())
    assert result.status_code == 500
    assert result.headers["cache-control"] == "no-store"
    assert SECRET not in result.text + caplog.text


def test_source_concurrency_budget_is_honest_unavailable(setup):
    client, fake, _ = setup
    capacity = client.app.state.service.capacity
    assert capacity.acquire(blocking=False)
    assert capacity.acquire(blocking=False)
    try:
        result = client.post("/api/search", json=query()).json()
        assert result["status"] == "unavailable"
        assert result["error"]["code"] == "BUSY"
        assert not fake.calls
    finally:
        capacity.release()
        capacity.release()


def test_generated_frontend_contract_has_no_drift():
    from scripts.export_web_contract import ROOT, generated

    assert (ROOT / "frontend/src/types.ts").read_text(encoding="utf-8") == generated()


def test_observed_two_room_handoff_defect_is_not_returned_to_browser(setup, monkeypatch):
    client, fake, _ = setup
    original = fake.call_tool

    def mismatched_link(name, args):
        response = original(name, args)
        if name == "getHotelDetail":
            response["structuredContent"]["bookingUrl"] = response["structuredContent"][
                "bookingUrl"
            ].replace("roomCount=2", "roomCount=1")
        return response

    monkeypatch.setattr(fake, "call_tool", mismatched_link)
    found = client.post("/api/search", json=query(rooms=2, children_ages=[5])).json()
    result = client.post("/api/hotels/17/quotes", json={"request_id": found["request_id"]}).json()
    assert result["status"] == "complete"  # quote browsing can remain available
    assert result["quotes"]
    assert result["booking_url"] is None
    assert result["booking_status"] == "mismatch"
    assert result["occupancy_status"] == "unverified"
    assert result["booking_context_issues"] == ["roomCount_mismatch"]
    assert result["occupancy_context_issues"] == [
        "adultCount_missing", "childCount_missing", "childAgeDetails_missing", "roomCount_missing"
    ]
    assert any("停用跳转" in warning for warning in result["warnings"])


def test_explicit_provider_occupancy_mismatch_rejects_quotes(setup, monkeypatch):
    client, fake, _ = setup
    original = fake.call_tool

    def wrong_occupancy(name, args):
        response = original(name, args)
        if name == "getHotelDetail":
            response["structuredContent"]["occupancyParam"] = {
                **args["occupancyParam"],
                "roomCount": 1,
            }
        return response

    monkeypatch.setattr(fake, "call_tool", wrong_occupancy)
    found = client.post("/api/search", json=query(rooms=2)).json()
    result = client.post("/api/hotels/17/quotes", json={"request_id": found["request_id"]}).json()
    assert result["status"] == "unavailable"
    assert result["quotes"] == []
    assert result["booking_url"] is None
    assert result["occupancy_status"] == "mismatch"
    assert result["occupancy_context_issues"] == ["roomCount_mismatch"]



@pytest.mark.parametrize(
    ("age_encoding", "expected_status", "expected_issue"),
    [
        ("[ ]", "matched", []),
        ("null", "unverified", ["childAgeDetails_unparseable"]),
        ("[5]", "mismatch", ["childAgeDetails_mismatch"]),
    ],
)
def test_full_api_preserves_zero_child_source_url_only_when_context_is_verified(
    setup, monkeypatch, age_encoding, expected_status, expected_issue,
):
    from urllib.parse import quote

    client, fake, _ = setup
    original = fake.call_tool
    source_url = []

    def supplier_detail(name, args):
        response = original(name, args)
        if name == "getHotelDetail":
            raw = response["structuredContent"]["bookingUrl"]
            assert "childAgeDetails=%5B%5D" in raw
            changed = raw.replace(
                "childAgeDetails=%5B%5D",
                "childAgeDetails=" + quote(age_encoding, safe=""),
            ) + "&providerTracking=keep-original"
            response["structuredContent"]["bookingUrl"] = changed
            source_url.append(changed)
        return response

    monkeypatch.setattr(fake, "call_tool", supplier_detail)
    found = client.post("/api/search", json=query(rooms=1, adults=2)).json()
    reply = client.post(
        "/api/hotels/17/quotes", json={"request_id": found["request_id"]}
    ).json()
    assert reply["status"] == "complete"
    assert reply["occupancy_status"] == "unverified"
    assert reply["booking_status"] == expected_status
    assert reply["booking_context_issues"] == expected_issue
    assert reply["booking_url"] == (
        source_url[0] if expected_status == "matched" else None
    )
    assert reply["quotes"]
    assert not any(
        "已核实库存" in warning or "完整费用已确认" in warning
        for warning in reply["warnings"]
    )


def blocked_fields(quote):
    return {item.field for item in quote.reference.items if item.status == "blocked"}


def test_every_rollinggo_quote_carries_a_reference_gate_rather_than_none():
    """A missing gate used to read as "not evaluated" on the page.

    RollingGo has no room-count field, so its reference gate must stay closed
    and must name the two missing fields instead of staying silent.
    """
    quote = room_quote(
        {"roomName": "大床房", "averagePrice": 188, "currency": "CNY"}, 0,
        rooms=2, snapshot_at="2026-11-06T00:00:00+00:00",
    )
    assert quote.reference is not None
    assert quote.reference.ready is False
    assert {"inventory_quantity", "occupancy_echo"} <= blocked_fields(quote)


def test_missing_room_name_cannot_borrow_the_whole_room_default():
    quote = room_quote({"averagePrice": 188, "currency": "CNY"}, 0)
    assert quote.room_name == "房型名称待核实"
    assert {"room_exclusivity", "private_bathroom"} <= blocked_fields(quote)


def test_dormitory_room_type_is_not_given_the_domestic_default():
    quote = room_quote(
        {"roomName": "青旅四人床位房", "averagePrice": 60, "currency": "CNY"}, 0,
    )
    assert {"room_exclusivity", "private_bathroom"} <= blocked_fields(quote)


def test_multi_night_rollinggo_quote_marks_the_breakdown_as_presumed_only():
    quote = room_quote(
        {"roomName": "大床房", "averagePrice": 188, "currency": "CNY"}, 0, nights=2,
    )
    status = {item.field: item.status for item in quote.reference.items}
    # An averaged price may be multiplied out, but only as a presumption.
    assert status["nightly_breakdown"] == "presumed"
    assert status["price_unit"] == "verified"


def test_the_inventory_note_describes_this_source_not_a_generic_rule():
    """The page prints these notes verbatim; a wrong one is a false claim."""
    quote = room_quote({"roomName": "大床房", "averagePrice": 188, "currency": "CNY"}, 0)
    row = next(item for item in quote.reference.items if item.field == "inventory_quantity")
    assert row.status == "blocked"
    assert "没有剩余房量字段" in row.note
    assert "来源自称剩余房量" not in row.note


def test_rollinggo_quotes_carry_a_blocked_booking_layer(setup):
    """r67: the booking verdict must travel with the response, not live in prose."""
    client, _, _ = setup
    request_id = client.post("/api/search", json=query()).json()["request_id"]
    result = client.post("/api/hotels/17/quotes", json={"request_id": request_id}).json()
    gate = result["bookability"]
    assert gate is not None
    assert gate["ready"] is False
    rows = {row["field"]: row for row in gate["items"]}
    # RollingGo really returns a matched verbatim link in this fixture.
    assert rows["safe_booking_link"]["status"] == "verified"
    assert "原样预订链接" in rows["safe_booking_link"]["note"]
    # But no stock field and no occupancy echo, so the layer stays blocked.
    assert rows["inventory_quantity"]["status"] == "blocked"
    assert rows["occupancy_echo"]["status"] == "blocked"
    assert rows["deposit_and_mandatory_fees"]["status"] == "blocked"


def test_a_runtime_link_mismatch_demotes_the_verified_link():
    """The static table cannot know this response's link failed its context check."""
    from stay_agent.qualification import booking_gate, gate_items

    # The endpoint turns a mismatch into CONTEXT_MISMATCH and returns no quotes,
    # so the runtime flag is exercised directly at the rule boundary instead.
    gate = booking_gate("rollinggo", nights=1, rooms=1, link_matched=False)
    rows = {row.field: row for row in gate_items(gate)}
    assert rows["safe_booking_link"].status == "blocked"
    assert "未通过参数核对" in rows["safe_booking_link"].note
    assert gate.ready is False


def test_rollinggo_quotes_never_qualify_without_a_stock_field(setup):
    """P6: no remaining-stock field means the best possible tier is 参考."""
    client, _, _ = setup
    request_id = client.post("/api/search", json=query()).json()["request_id"]
    result = client.post("/api/hotels/17/quotes", json={"request_id": request_id}).json()
    assert result["quotes"]
    for row in result["quotes"]:
        qualification = row["qualification"]
        assert qualification is not None
        inventory = next(c for c in qualification["conditions"] if c["condition"] == "inventory")
        assert inventory["verdict"] == "unknown"
        assert qualification["tier"] in ("reference", "excluded")
    assert result["qualified_lowest_id"] is None
