"""FlyAI is reachable from the product as a discovery-only source.

Every assertion here is offline: the supplier call is injected, so no request
budget is spent and no credential is needed.
"""
from datetime import timedelta
import threading

import pytest
from fastapi.testclient import TestClient

from stay_agent.contracts import SearchInput, china_today
from stay_agent.discovery_service import LIMITATIONS, DiscoveryError, DiscoveryService
from stay_agent.flyai_source import make_flyai_adapter
from stay_agent.main import create_app
from stay_agent.providers.data.flyai import FlyAIDiscoveryProvider
from stay_agent.providers.data.tuniu import TuniuFailure
from stay_agent.storage import SessionStore


def row(**extra):
    base = {
        "shId": "82213056",
        "name": "某民宿",
        "address": "广州-越秀区-某路1号",
        "price": "¥144",
        "latitude": 23.133191,
        "longitude": 113.285472,
        "star": "经济型",
        "interestsPoi": "近某地铁站",
        "detailUrl": "https://router.example.invalid/multi/webview?url=x",
    }
    base.update(extra)
    return base


def payload(rows, *, status=0):
    return {"status": status, "message": "success", "data": {"itemList": rows}}


def query(**extra):
    today = china_today()
    base = {
        "place": "广州",
        "place_type": "城市",
        "check_in": today,
        "check_out": today + timedelta(days=1),
    }
    base.update(extra)
    return SearchInput(**base)


def provider(rows, *, mode="offline_test", calls=None):
    def call(args):
        if calls is not None:
            calls.append(args)
        if isinstance(rows, Exception):
            raise rows
        return payload(rows)

    return FlyAIDiscoveryProvider(call, mode=mode)


def service(rows, **kwargs):
    return DiscoveryService(make_flyai_adapter(provider(rows)), **kwargs)


def test_candidates_keep_display_price_as_text_and_never_as_money():
    result = service([row()]).search(query())
    assert result.status == "complete"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.name == "某民宿"
    assert candidate.price_text == "¥144"
    assert candidate.star == "经济型"
    assert candidate.nearby_transit == "近某地铁站"
    dumped = result.model_dump()
    assert "merged" not in dumped
    assert "reference_price_fen" not in dumped["candidates"][0]


def test_coordinates_are_reduced_to_a_boolean():
    with_coords = service([row()]).search(query())
    without = service([row(latitude=None, longitude=None)]).search(query())
    assert with_coords.candidates[0].coordinates_present is True
    assert without.candidates[0].coordinates_present is False
    assert "latitude" not in with_coords.model_dump()["candidates"][0]


def test_response_states_that_it_is_not_mergeable():
    result = service([row()]).search(query())
    assert result.source.id == "flyai" and result.source.name == "飞猪"
    assert any("合并" in line for line in result.limitations)
    assert set(LIMITATIONS).issubset(set(result.limitations))


def test_the_query_scope_is_stated_and_occupancy_is_not_claimed():
    result = service([row()]).search(query())
    scope = result.limitations[0]
    assert "广州" in scope and "人数与房间数没有传给飞猪" in scope


def test_only_a_city_place_is_accepted():
    with pytest.raises(DiscoveryError) as error:
        service([row()]).search(query(place="某酒店", place_type="酒店"))
    assert error.value.code == "DISCOVERY_CITY_ONLY"


def test_missing_credential_is_reported_as_unconfigured_not_empty():
    unconfigured = DiscoveryService(make_flyai_adapter(provider([], mode="disabled")))
    result = unconfigured.search(query())
    assert result.status == "unconfigured"
    assert result.candidates == []
    assert result.error is not None
    assert result.limitations[0].startswith("飞猪凭据未配置")


def test_cancellation_is_explicit():
    token = threading.Event()
    token.set()
    result = service([row()]).search(query(), cancelled=token)
    assert result.status == "cancelled"
    assert result.error is not None


def test_supplier_failure_keeps_a_named_status():
    failed = DiscoveryService(make_flyai_adapter(provider(TuniuFailure("TIMEOUT"))))
    result = failed.search(query())
    assert result.status == "unavailable"
    assert result.candidates == []


@pytest.fixture
def client(tmp_path):
    class Quote:
        data_mode = "test"

        def credential_configured(self):
            return True

    app = create_app(
        service=Quote(),
        session_store=SessionStore(tmp_path / "sessions.sqlite3"),
        web_root=tmp_path / "web",
        discovery_service=service([row()]),
    )
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def body(**extra):
    today = china_today()
    base = {
        "place": "广州",
        "place_type": "城市",
        "check_in": today.isoformat(),
        "check_out": (today + timedelta(days=1)).isoformat(),
    }
    base.update(extra)
    return base


def test_endpoint_returns_candidates_without_touching_quote_state(client):
    response = client.post("/api/discovery", json=body())
    assert response.status_code == 200
    result = response.json()
    assert result["source"] == {"id": "flyai", "name": "飞猪"}
    assert result["candidates"][0]["price_text"] == "¥144"
    assert "hotels" not in result


def test_endpoint_refuses_non_city_place_with_a_named_code(client):
    response = client.post("/api/discovery", json=body(place="某酒店", place_type="酒店"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DISCOVERY_CITY_ONLY"
