"""Offline shape-probe privacy and observational boundaries."""

import json
from scripts.evaluation.r111 import city_shape


def test_city_shape_retains_only_types_and_lexical_categories():
    raw = {
        "success": True,
        "hotelInformationList": [
            {
                "hotelId": 123,
                "name": "PRIVATE-HOTEL",
                "city": "广州市",
                "cityName": "北京市",
                "cityCode": "SECRET-CODE",
                "province": "PRIVATE-PROVINCE",
                "areaCode": "PRIVATE-COUNTRY",
                "destinationId": 456,
                "address": "广州市PRIVATE-STREET",
                "bookingUrl": "https://example.test/token=PRIVATE",
                "latitude": 23.123,
                "unknown": "PRIVATE-OTHER",
            }
        ],
    }
    summary = city_shape(raw)
    row = summary["candidates"][0]
    assert row["fields"]["city"]["lexical_class"] == "requested_city_text"
    assert row["fields"]["cityName"]["lexical_class"] == "different_text"
    assert row["city_text_fields_conflict"] is True
    assert row["fields"]["cityCode"]["type"] == "string"
    assert row["independent_city_verified"] is False
    dumped = json.dumps(summary)
    for secret in ("PRIVATE", "SECRET-CODE", "hotelId", "latitude", "bookingUrl", "unknown"):
        assert secret not in dumped


def test_code_and_country_cannot_be_upgraded_to_city():
    summary = city_shape(
        {"hotelInformationList": [{"areaCode": "CN", "destinationId": 123, "cityCode": "440100"}]}
    )
    row = summary["candidates"][0]
    assert row["independent_city_verified"] is False
    assert not row["fields"]["city"]["present"]
    assert "lexical_class" not in row["fields"]["cityCode"]


def test_invalid_and_excess_records_are_counted_without_dumping_them():
    summary = city_shape({"hotelInformationList": [None] + [{}] * 12})
    assert summary["returned_count"] == 13 and summary["inspected_count"] == 10
    assert summary["truncated"] is True
    assert summary["candidates"][0]["valid_record"] is False


def test_nested_city_objects_are_unknown_not_invented_evidence():
    summary = city_shape({"hotelInformationList": [{"city": {"name": "广州市"}, "address": None}]})
    field = summary["candidates"][0]["fields"]["city"]
    assert field["type"] == "object" and field["lexical_class"] == "unknown"
    assert summary["candidates"][0]["independent_city_verified"] is False


def test_replay_marker_rejects_before_any_source_or_budget_use(tmp_path):
    import pytest
    from scripts.evaluation.ledger import Budget
    from scripts.evaluation.r111 import run

    budget = Budget.create(tmp_path / "budget.sqlite", {"rollinggo": 4})
    (tmp_path / "probe.started").touch()
    with pytest.raises(FileExistsError):
        run(tmp_path, budget)
    assert budget.summary()["rollinggo"]["attempted"] == 0


def test_observer_allows_only_one_search_even_if_caller_tries_other_tools():
    import pytest
    from scripts.evaluation.r111 import ShapeClient

    class FakeClient:
        def call_tool(self, name, arguments):
            return {"structuredContent": {"success": True, "hotelInformationList": []}}

        def redact(self, result):
            return result

    client = ShapeClient(FakeClient())
    client.call_tool("searchHotels", {})
    for name in ("searchHotels", "getHotelDetail", "createOrder"):
        with pytest.raises(ValueError, match="ONE_SEARCH_ONLY"):
            client.call_tool(name, {})
