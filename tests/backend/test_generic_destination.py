"""Mainland-only destination contract; no overseas supplier lookup."""
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from stay_agent.contracts import SearchInput


def input_for(**updates):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    data = dict(
        place="上海迪士尼乐园", place_type="景点", country_region="中国大陆",
        waypoints=["苏州北站", "无锡东站"], check_in=today+timedelta(days=1),
        check_out=today+timedelta(days=2),
    )
    data.update(updates)
    return SearchInput(**data)


def test_mainland_destination_and_itinerary_notes():
    query = input_for()
    assert query.place == "上海迪士尼乐园"
    assert query.country_region == "中国大陆"
    assert query.waypoints == ["苏州北站", "无锡东站"]
    assert query.poi_name is None


@pytest.mark.parametrize("waypoints", [
    [""], ["苏州北站", "苏州北站"], ["a"]*6, ["a"+chr(10)+"secret"],
])
def test_bad_waypoints_fail_before_supplier_call(waypoints):
    with pytest.raises(ValidationError):
        input_for(waypoints=waypoints)


@pytest.mark.parametrize("region", [
    "日本", "美国", "法国", "香港", "澳门", "台湾", "Hong Kong", "Macau", "Taiwan"
])
def test_overseas_or_non_mainland_region_is_rejected_by_contract(region):
    with pytest.raises(ValidationError, match="only mainland China"):
        input_for(country_region=region)


def test_legacy_missing_region_can_be_deserialized_but_new_ui_always_supplies_mainland():
    assert input_for(country_region=None).country_region is None


def test_public_search_rejects_explicit_overseas_before_supplier_http(tmp_path):
    from fastapi.testclient import TestClient
    from stay_agent.main import create_app
    from stay_agent.service import QuoteService
    from stay_agent.storage import SessionStore

    attempts=[]
    def forbidden_client():
        attempts.append(True)
        raise AssertionError("supplier must not be called for non-mainland request")
    app=create_app(
        service=QuoteService(client_factory=forbidden_client),
        session_store=SessionStore(tmp_path/"mainland.sqlite3"),
        web_root=tmp_path/"web",
    )
    client=TestClient(app,base_url="http://127.0.0.1")
    payload=input_for().model_dump(mode="json")
    payload["country_region"]="日本"
    rejected=client.post("/api/search",json=payload)
    assert rejected.status_code==422
    assert not attempts
