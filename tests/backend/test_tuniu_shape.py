"""No-network verification of the allowlisted Tuniu detail shape diagnostic."""
import json
from types import SimpleNamespace

from scripts.verify_tuniu_p4_positive import safe_detail_shape


def _identity():
    return (
        SimpleNamespace(
            hotel_id=71, name="synthetic-name-do-not-print",
            address="synthetic-address-do-not-print", city="广州",
        ),
        SimpleNamespace(
            check_in="2026-11-13", check_out="2026-11-14",
            adults_per_room=2, rooms=1,
        ),
    )


def test_detail_shape_does_not_export_source_identity_or_prebookparam():
    hotel, scope = _identity()
    secret = "synthetic-sensitive-preBookParam-abc123"
    payload = {
        "structuredContent": {
            "success": True, "hotelId": hotel.hotel_id,
            "hotelName": hotel.name, "address": hotel.address,
            "checkIn": scope.check_in, "checkOut": scope.check_out,
            "adultNum": 2, "roomNum": 1,
            "roomTypes": [
                {
                    "roomTypeId": "unknown",
                    "ratePlans": [{
                        "preBookParam": secret, "rmbPrices": "600",
                        "cancelDesc": "private source field",
                    }],
                },
            ],
        },
    }
    shape = safe_detail_shape(payload, hotel, scope)
    assert shape["projection"] == "SUCCESS"
    assert shape["hotel_id_matches_search"]
    assert shape["hotel_name_matches_search"]
    assert shape["address_matches_search"]
    assert shape["room_types_count"] == 1
    assert shape["room_rate_plan_list_count"] == 1
    assert shape["adult_echo_matches"] is True
    serialized = json.dumps(shape, ensure_ascii=False)
    for forbidden in (
        secret, hotel.name, hotel.address, "preBookParam",
        "rmbPrices", "cancelDesc", "private source field", '"71"',
    ):
        assert forbidden not in serialized


def test_missing_roomtypes_is_exposed_only_as_safe_booleans():
    hotel, scope = _identity()
    shape = safe_detail_shape(
        {"structuredContent": {"success": True, "data": {
            "roomTypes": [{"preBookParam": "hidden"}],
        }}}, hotel, scope,
    )
    assert shape["projection"] == "SUCCESS"
    assert shape["room_types_list"] is False
    assert shape["nested_data_mapping"] is True
    assert shape["nested_data_room_types_list"] is True
    assert "hidden" not in str(shape)


def test_mcp_error_is_classified_without_payload_text():
    hotel, scope = _identity()
    shape = safe_detail_shape(
        {"isError": True, "content": [{
            "type": "text", "text": "raw-secret-access-key",
        }]}, hotel, scope,
    )
    assert shape["mcp_is_error"] is True
    assert shape["projection"] == "SOURCE_ERROR"
    assert "raw-secret" not in str(shape)


def test_plain_success_response_is_supported_without_producing_booking_data():
    hotel, scope = _identity()
    shape = safe_detail_shape(
        {"success": True, "hotelId": hotel.hotel_id,
         "hotelName": hotel.name, "address": hotel.address,
         "roomTypes": []}, hotel, scope,
    )
    assert shape["projection"] == "SUCCESS"
    assert shape["room_types_count"] == 0
    assert shape["city_echo_matches_search"] is None
    assert shape["room_count_echo_matches"] is None


def test_text_content_json_stage_is_reported_without_values():
    hotel, scope = _identity()
    secret = "hidden-content-secret"
    payload = {
        "content": [{"type": "text", "text": json.dumps({
            "success": True, "hotelId": hotel.hotel_id,
            "hotelName": hotel.name, "address": hotel.address,
            "roomTypes": [{"roomTypeId": "x", "ratePlans": [
                {"preBookParam": secret, "rmbPrices": "600"}]}],
        })}],
    }
    shape = safe_detail_shape(payload, hotel, scope)
    assert shape["projection"] == "SUCCESS"
    assert shape["response_stage"] == "text_json_flat_success"
    assert shape["content_item_count"] == 1
    assert shape["text_item_count"] == 1
    assert shape["text_length"] > 0
    assert shape["room_types_count"] == 1
    serialized = json.dumps(shape, ensure_ascii=False)
    assert secret not in serialized and hotel.name not in serialized


def test_wrapper_success_next_to_content_is_still_classified_as_text_json():
    hotel, scope = _identity()
    payload = {
        "success": True,
        "content": [{"type": "text", "text": json.dumps({
            "success": True, "hotelId": hotel.hotel_id,
            "hotelName": hotel.name, "address": hotel.address,
            "roomTypes": [],
        })}],
    }
    shape = safe_detail_shape(payload, hotel, scope)
    assert shape["projection"] == "SUCCESS"
    assert shape["response_stage"] == "text_json_flat_success"
    assert shape["content_item_count"] == 1


def test_detail_text_without_success_is_accepted_by_documented_shape():
    hotel, scope = _identity()
    payload = {"content": [{"type": "text", "text": json.dumps({
        "hotelId": hotel.hotel_id, "hotelName": hotel.name,
        "address": hotel.address, "cityName": hotel.city,
        "roomTypes": [{"roomTypeId": "x", "ratePlans": []}],
    })}]}
    shape = safe_detail_shape(payload, hotel, scope)
    assert shape["projection"] == "SUCCESS"
    assert shape["response_stage"] == "text_json_detail_shape"
    assert shape["hotel_id_matches_search"]
    assert shape["hotel_name_matches_search"]
    assert shape["address_matches_search"]
    assert shape["room_types_count"] == 1


def test_detail_text_with_explicit_success_false_is_source_error():
    hotel, scope = _identity()
    shape = safe_detail_shape(
        {"content": [{"type": "text", "text": json.dumps({
            "success": False, "hotelId": hotel.hotel_id,
        })}]}, hotel, scope,
    )
    assert shape["projection"] == "SOURCE_ERROR"
    assert shape["response_stage"] == "text_json_success_false"


def test_text_content_failure_stages_are_safe_labels():
    hotel, scope = _identity()
    cases = [
        ({"content": [{"type": "text", "text": "{}"},
                      {"type": "text", "text": "{}"}]}, "text_count_2"),
        ({"content": [{"type": "text", "text": "{not-json"}]},
         "text_json_parse_failed"),
        ({"content": [{"type": "text", "text": json.dumps({"message": "no detail markers"})}]},
         "text_json_success_missing"),
        ({"content": [{"type": "text", "text": "x" * 600001}]},
         "text_over_600000"),
        ({"content": [{"type": "text", "text": "{}"}] + [{"type": "text", "text": "{}"}] * 8},
         "content_over_8"),
    ]
    for payload, stage in cases:
        shape = safe_detail_shape(payload, hotel, scope)
        assert shape["projection"] == "INVALID_RESPONSE"
        assert shape["response_stage"] == stage
        assert "x" * 100 not in json.dumps(shape)
