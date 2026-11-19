"""Offline guards for credential-bearing travel probes; no real source calls."""

import json
import ssl
from urllib.parse import quote_plus

import pytest

from stay_agent.providers.data.rollinggo_client import TransportResponse
from stay_agent.providers.data.travel_clients import (
    DidiDiscovery,
    MapProbe,
    Point,
    ProbeError,
    SecureHTTP,
    load_key,
    route_summary,
)

SECRET = "synthetic-key/+?never-real"


def test_local_key_loading_quotes_and_process_precedence(tmp_path):
    path = tmp_path / "local.env"
    path.write_text(
        "AMAP_API_KEY='fixture-amap'\nTENCENT_MAP_KEY=fixture-tencent\n", encoding="utf-8"
    )
    assert load_key("amap", path, {}) == "fixture-amap"
    assert load_key("amap", path, {"AMAP_API_KEY": "process-key"}) == "process-key"
    assert load_key("tencent", path, {}) == "fixture-tencent"


@pytest.mark.parametrize(
    "value",
    [
        "YOUR_MCP_KEY",
        "https://mcp.didichuxing.com/mcp-servers?key=private",
        "Bearer private",
        "bad\tkey",
    ],
)
def test_full_urls_placeholders_and_headers_are_not_treated_as_keys(tmp_path, value):
    path = tmp_path / "empty.env"
    with pytest.raises(ProbeError) as caught:
        load_key("didi", path, {"DIDI_MCP_KEY": value})
    assert value not in str(caught.value)


def test_duplicate_credentials_fail_without_echoing_values(tmp_path):
    path = tmp_path / "local.env"
    path.write_text("DIDI_MCP_KEY=first\nDIDI_MCP_KEY=second", encoding="utf-8")
    with pytest.raises(ProbeError, match="DUPLICATE_CREDENTIAL"):
        load_key("didi", path, {})
    assert "first" not in str(ProbeError("DUPLICATE_CREDENTIAL"))


def test_tencent_duration_is_minutes_and_boundary_rounds_conservatively():
    data = {
        "result": {
            "routes": [{"duration": 10, "distance": 800}, {"duration": 10.01, "distance": 801}]
        }
    }
    routes = route_summary("tencent", data)
    assert [r["duration_seconds"] for r in routes] == [600, 601]
    assert routes[0]["source_duration_unit"] == "minutes"
    assert (
        route_summary("amap", {"route": {"paths": [{"duration": "601", "distance": "700"}]}})[0][
            "duration_seconds"
        ]
        == 601
    )


@pytest.mark.parametrize("duration", [True, None, "NaN", "Infinity", -1])
def test_unknown_duration_is_not_zero_or_a_qualified_route(duration):
    assert (
        route_summary("tencent", {"result": {"routes": [{"duration": duration, "distance": 10}]}})
        == []
    )


class FakeMaps:
    def __init__(self):
        self.calls = []

    def json(self, operation, params):
        self.calls.append((operation, params))
        if operation == "search":
            return {
                "status": 0,
                "data": [
                    {"id": "one", "title": "synthetic place", "location": {"lat": 23, "lng": 113}}
                ],
            }
        return {"status": 0, "result": {"routes": [{"duration": 10, "distance": 800}]}}


def test_walk_uses_same_provider_live_search_points_and_correct_order():
    fake = FakeMaps()
    client = MapProbe("tencent", "not-a-real-key", http=fake)
    point = client.search("海底捞", "广州")[0]
    assert fake.calls[0][1]["boundary"] == "region(广州,0)"
    assert client.walk(point, point)[0]["duration_seconds"] == 600
    assert fake.calls[-1][1] == {"from": "23.000000,113.000000", "to": "23.000000,113.000000"}
    count = len(fake.calls)
    foreign = Point("amap", "one", "synthetic place", 113, 23)
    with pytest.raises(ProbeError, match="FOREIGN_OR_UNSEEN_COORDINATE"):
        client.walk(foreign, point)
    unseen = Point("tencent", "other", "synthetic place", 113, 23)
    with pytest.raises(ProbeError, match="FOREIGN_OR_UNSEEN_COORDINATE"):
        client.walk(unseen, point)
    assert len(fake.calls) == count


def test_false_is_not_tencent_business_success():
    class Rejected:
        def json(self, operation, params):
            return {"status": False, "data": []}

    with pytest.raises(ProbeError, match="PROVIDER_REJECTED"):
        MapProbe("tencent", "fixture", http=Rejected()).search("海底捞")


def test_didi_discovery_only_lists_and_drops_transaction_schemas():
    class FakeHTTP:
        request_count = 0

        def open(self, operation, params=None, body=None, headers=None):
            self.request_count += 1
            assert operation == "rpc"
            assert not any(k.lower() == "authorization" for k in headers)
            request = json.loads(body)
            assert request["method"] == "tools/list"
            tools = [
                {"name": name, "inputSchema": {}}
                for name in (
                    "maps_textsearch",
                    "taxi_estimate",
                    "taxi_create_order",
                    "taxi_query_order",
                )
            ]
            return TransportResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": {"tools": tools}}
                ).encode(),
            )

    client = DidiDiscovery(SECRET, http=FakeHTTP())
    assert {t["name"] for t in client.discover()} == {"maps_textsearch", "taxi_estimate"}


class FakeSock:
    def settimeout(self, value):
        pass


class FakeResponse:
    status = 302

    def getheaders(self):
        return [("Location", "https://evil.invalid/")]

    def read1(self, n):
        return b""

    def close(self):
        pass


def test_tls_is_verified_and_redirect_never_followed(monkeypatch):
    calls = []

    class Connection:
        sock = FakeSock()

        def __init__(self, host, port, timeout, context):
            calls.append((host, port))
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

        def request(self, method, path, body, headers):
            assert method == "POST" and path.startswith("/mcp-servers?key=")
            assert quote_plus(SECRET) in path

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr("stay_agent.providers.data.travel_clients.http.client.HTTPSConnection", Connection)
    with pytest.raises(ProbeError, match="REDIRECT_DENIED") as caught:
        SecureHTTP("didi", SECRET).open("rpc", body=b"{}")
    assert calls == [("mcp.didichuxing.com", 443)]
    assert SECRET not in str(caught.value)


def test_transport_denies_unknown_targets_and_key_override_before_network():
    transport = SecureHTTP("amap", SECRET, max_requests=1)
    for operation, params in [("https://evil.invalid", {}), ("search", {"key": "override"})]:
        with pytest.raises(ProbeError):
            transport.open(operation, params)
    assert transport.request_count == 0
    transport.request_count = 1
    with pytest.raises(ProbeError, match="REQUEST_LIMIT"):
        transport.open("search")


def test_transport_exception_message_cannot_expose_key_or_url(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("https://host/?key=" + SECRET)

    monkeypatch.setattr("stay_agent.providers.data.travel_clients.http.client.HTTPSConnection", fail)
    with pytest.raises(ProbeError) as caught:
        SecureHTTP("tencent", SECRET).open("search")
    assert SECRET not in str(caught.value)


def test_tencent_signature_vector_uses_raw_sorted_unicode_parameters():
    from stay_agent.providers.data.travel_clients import tencent_signature

    params = {"keyword": "海底捞&咖啡#测试", "key": "fixture-key", "boundary": "region(广州,0)"}
    # Reference computed independently with Node crypto on Tencent's documented canonical string.
    assert (
        tencent_signature("/ws/place/v1/search", params, "fixture-secret")
        == "15647533b28ab34e00682b3ecd21727c"
    )


def test_signed_get_keeps_secret_out_of_wire_and_encodes_values(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    captured = {}

    class Response:
        status = 200

        def getheaders(self):
            return [("content-type", "application/json")]

        def close(self):
            pass

    class Connection:
        sock = FakeSock()

        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, body, headers):
            captured.update(method=method, path=path, headers=headers)

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("stay_agent.providers.data.travel_clients.http.client.HTTPSConnection", Connection)
    response = SecureHTTP("tencent", "fixture-key", secret="fixture-secret").open(
        "search", {"keyword": "海底捞&咖啡#测试", "boundary": "region(广州,0)"}
    )
    response.close()
    parsed = parse_qs(urlsplit(captured["path"]).query)
    assert parsed["keyword"] == ["海底捞&咖啡#测试"]
    assert parsed["sig"] == ["15647533b28ab34e00682b3ecd21727c"]
    assert "fixture-secret" not in str(captured)
    assert "海底捞" not in captured["path"]
    assert captured["headers"]["x-legacy-url-decode"] == "no"


def test_signing_secret_is_optional_and_not_a_map_api_key(tmp_path):
    from stay_agent.providers.data.travel_clients import load_tencent_secret

    path = tmp_path / "local.env"
    path.write_text("TENCENT_MAP_KEY=fixture-key\n", encoding="utf-8")
    assert load_tencent_secret(path, {}) is None
    path.write_text(
        "TENCENT_MAP_KEY=fixture-key\nTENCENT_MAP_SECRET='fixture-secret'\n", encoding="utf-8"
    )
    assert load_tencent_secret(path, {}) == "fixture-secret"
    assert load_key("tencent", path, {}) == "fixture-key"


def test_supplied_signature_cannot_override_server_calculation():
    transport = SecureHTTP("tencent", "fixture-key", secret="fixture-secret")
    with pytest.raises(ProbeError, match="SIGNATURE_OVERRIDE_DENIED"):
        transport.open("search", {"keyword": "酒店", "sig": "supplied"})
    assert transport.request_count == 0


def test_connection_close_after_complete_body_does_not_break_json(monkeypatch):
    from stay_agent.providers.data.travel_clients import SecureHTTP

    class Response:
        status = 200
        closed = False

        def getheaders(self):
            return [("content-type", "application/json")]

        def isclosed(self):
            return self.closed

        def read1(self, n):
            if self.closed:
                return b""
            self.closed = True
            return b'{"status":0,"data":[]}'

        def close(self):
            self.closed = True

    response = Response()

    class Sock:
        def settimeout(self, value):
            if response.closed:
                raise OSError("closed socket")

    class Connection:
        sock = Sock()

        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return response

        def close(self):
            pass

    monkeypatch.setattr("stay_agent.providers.data.travel_clients.http.client.HTTPSConnection", Connection)
    assert SecureHTTP("tencent", "fixture-key").json("search", {}) == {"status": 0, "data": []}


def test_didi_array_payload_and_estimates_require_its_own_points():
    from stay_agent.providers.data.travel_clients import decode_didi, DidiDiscovery

    assert decode_didi({"content": [{"type": "text", "text": '[{"display_name":"fixture"}]'}]}) == [
        {"display_name": "fixture"}
    ]
    client = DidiDiscovery("fixture-key")
    with pytest.raises(ProbeError, match="FOREIGN_OR_UNSEEN_COORDINATE"):
        client.call_readonly(
            "taxi_estimate",
            {
                "from_lng": "113",
                "from_lat": "23",
                "from_name": "fixture",
                "to_lng": "113",
                "to_lat": "23",
                "to_name": "fixture",
            },
        )
    with pytest.raises(ProbeError, match="OPERATION_DENIED"):
        client.call_readonly("taxi_create_order", {})


def test_provider_pacing_is_shared_between_contexts_but_not_providers():
    from stay_agent.providers.data.travel_clients import RequestPacer

    clock = [100.0]
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    pacer = RequestPacer(clock=lambda: clock[0], sleep=sleep)

    def remaining():
        return 15

    pacer.wait("amap", 1.1, remaining)
    pacer.wait("tencent", 1.1, remaining)
    pacer.wait("amap", 1.1, remaining)
    assert sleeps == [pytest.approx(1.1)]
    with pytest.raises(ProbeError, match="TIMEOUT"):
        pacer.wait("amap", 1.1, lambda: 0.1)


def test_map_projection_redacts_echoed_key_and_signing_secret():
    class Echo:
        _secret = "fixture-private-signing-secret"

        def json(self, operation, params):
            return {
                "status": 0,
                "data": [
                    {
                        "id": "one",
                        "title": "fixture-private-map-key",
                        "address": self._secret,
                        "city": "广州",
                        "location": {"lng": 113, "lat": 23},
                    }
                ],
            }

    client = MapProbe("tencent", "fixture-private-map-key", http=Echo())
    places = client.search("hotel", "广州")
    projected = str((places, client.metadata))
    assert "fixture-private-map-key" not in projected
    assert "fixture-private-signing-secret" not in projected


class FakeAmapDriving:
    def __init__(self, duration="600", distance="1600"):
        self.calls = []
        self.duration = duration
        self.distance = distance

    def json(self, operation, params):
        self.calls.append((operation, params))
        if operation == "search":
            return {
                "status": "1",
                "pois": [{
                    "id": "amap-hotel",
                    "name": "synthetic hotel",
                    "location": "113.000000,23.000000",
                    "address": "synthetic address",
                    "cityname": "广州市",
                    "type": "住宿服务;宾馆酒店",
                    "typecode": "100100",
                }],
            }
        assert operation == "drive"
        return {
            "status": "1",
            "route": {
                "paths": [{
                    "duration": self.duration,
                    "distance": self.distance,
                }]
            },
        }


@pytest.mark.parametrize(
    ("duration", "expected"),
    [("600", 600), ("601", 601)],
)
def test_amap_drive_returns_seconds_and_uses_same_provider_registered_points(duration, expected):
    fake = FakeAmapDriving(duration=duration)
    client = MapProbe("amap", "not-a-real-key", http=fake)
    point = client.search("synthetic hotel", "广州")[0]
    routes = client.drive(point, point)
    assert routes[0]["duration_seconds"] == expected
    assert routes[0]["distance_meters"] == 1600
    assert routes[0]["source_duration_unit"] == "seconds"
    operation, arguments = fake.calls[-1]
    assert operation == "drive"
    assert arguments == {
        "origin": "113.000000,23.000000",
        "destination": "113.000000,23.000000",
        "strategy": 0,
        "extensions": "base",
        "output": "json",
    }


def test_amap_drive_rejects_unseen_or_foreign_points_before_http():
    fake = FakeAmapDriving()
    client = MapProbe("amap", "not-a-real-key", http=fake)
    point = client.search("synthetic hotel", "广州")[0]
    count = len(fake.calls)
    foreign = Point("tencent", "foreign", "foreign place", 113, 23)
    with pytest.raises(ProbeError, match="FOREIGN_OR_UNSEEN_COORDINATE"):
        client.drive(point, foreign)
    unseen = Point("amap", "unseen", "unseen place", 113, 23)
    with pytest.raises(ProbeError, match="FOREIGN_OR_UNSEEN_COORDINATE"):
        client.drive(point, unseen)
    assert len(fake.calls) == count


def test_tencent_drive_uses_authorized_provider_endpoint_minutes_and_registered_points():
    from stay_agent.providers.data.travel_clients import ENDPOINTS

    assert ENDPOINTS[("tencent", "drive")] == (
        "apis.map.qq.com", "/ws/direction/v1/driving/",
    )
    fake = FakeMaps()
    client = MapProbe("tencent", "not-a-real-key", http=fake)
    point = client.search("海底捞", "广州")[0]
    routes = client.drive(point, point)
    assert routes[0]["duration_seconds"] == 600
    assert routes[0]["distance_meters"] == 800
    assert routes[0]["source_duration_unit"] == "minutes"
    assert fake.calls[-1] == (
        "drive", {"from": "23.000000,113.000000", "to": "23.000000,113.000000"},
    )
    count = len(fake.calls)
    with pytest.raises(ProbeError, match="FOREIGN_OR_UNSEEN_COORDINATE"):
        client.drive(point, Point("amap", "foreign", "synthetic", 113, 23))
    with pytest.raises(ProbeError, match="FOREIGN_OR_UNSEEN_COORDINATE"):
        client.drive(point, Point("tencent", "unknown", "synthetic", 113, 23))
    assert len(fake.calls) == count


def test_tencent_drive_fractional_minutes_over_boundary_rounds_up():
    rows = route_summary("tencent", {
        "result": {"routes": [{"duration": 15, "distance": 2400},
                              {"duration": 15.01, "distance": 2401}]}
    })
    assert [item["duration_seconds"] for item in rows] == [900, 901]
