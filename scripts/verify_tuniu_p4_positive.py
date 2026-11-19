"""One-shot bounded P4 Tuniu *live* search + optional same-snapshot detail.

Only --live can send traffic. In that mode no more than 5 raw transport
attempts are permitted, including MCP initialize, notification and discovery.
The process never retries, paginates, books, saves raw supplier responses,
displays original provider tokens or opens unverified booking links.

This run uses an intentionally BROADER ¥500 reference-price search than the
product's ¥200 hard budget, only to test whether a live hotel/detail can be
observed. It must NOT be presented as qualification for the ¥200 product.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.contracts import SearchInput  # noqa: E402
from stay_agent.providers.data.rollinggo_client import RollingGoError  # noqa: E402
from stay_agent.providers.data.tuniu import (  # noqa: E402
    DETAIL_TOOL, SEARCH_TOOL, TuniuFailure, TuniuProvider,
    _positive_id, _source_call, _text,
)
from stay_agent.providers.data.tuniu_mcp import (  # noqa: E402
    TuniuConfigurationError, TuniuMCPClient, auth_configuration, make_transport,
)

_MAX_ATTEMPTS = 5
_ALLOWED = frozenset(("initialize", "notifications/initialized", "tools/list", "tools/call"))


def _response_stage(result) -> str:
    """Mirror _source_call's fixed branches as a safe label; never expose values."""
    if not isinstance(result, dict):
        return "not_mapping"
    if result.get("isError") is True:
        return "mcp_error"
    if result.get("success") is False:
        return "flat_success_false"
    if isinstance(result.get("structuredContent"), dict):
        return "structured_mapping"
    if "content" in result and not any(
        key in result for key in ("hotels", "queryId", "hotelId", "roomTypes")
    ):
        content = result.get("content")
        if not isinstance(content, list):
            return "content_not_list"
        if len(content) > 8:
            return "content_over_8"
        texts = [item.get("text") for item in content
                 if isinstance(item, dict) and item.get("type") == "text"]
        if len(texts) != 1:
            return "text_count_" + str(min(len(texts), 9))
        text = texts[0]
        if not isinstance(text, str):
            return "text_not_str"
        if len(text) > 600000:
            return "text_over_600000"
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return "text_json_parse_failed"
        if not isinstance(parsed, dict):
            return "text_json_not_dict"
        if parsed.get("success") is False:
            return "text_json_success_false"
        if parsed.get("success") is not True:
            # Official docs: the detail response has no `success`; the adapter
            # accepts it only with detail markers.
            if any(key in parsed for key in ("hotelId", "roomTypes")):
                return "text_json_detail_shape"
            return "text_json_success_missing"
        return "text_json_flat_success"
    if result.get("success") is False:
        return "flat_success_false"
    if result.get("success") is not True:
        if any(key in result for key in ("hotelId", "roomTypes")):
            return "flat_detail_shape"
        return "flat_success_missing"
    return "flat_success"


def safe_detail_shape(result, candidate, scope) -> dict:
    """Only fixed booleans/counts, never keys/values from arbitrary source."""
    content = result.get("content") if isinstance(result, dict) else None
    texts = ([item.get("text") for item in content
              if isinstance(item, dict) and item.get("type") == "text"]
             if isinstance(content, list) else [])
    info = {
        "mcp_mapping": isinstance(result, dict),
        "mcp_is_error": isinstance(result, dict) and result.get("isError") is True,
        "structured_mapping": isinstance(result, dict)
        and isinstance(result.get("structuredContent"), dict),
        "mcp_content_list": isinstance(content, list),
        "content_item_count": min(len(content), 9) if isinstance(content, list) else None,
        "text_item_count": min(len(texts), 9) if isinstance(content, list) else None,
        "text_length": len(texts[0]) if len(texts) == 1 and isinstance(texts[0], str) else None,
        "response_stage": _response_stage(result),
    }
    try:
        payload = _source_call(DETAIL_TOOL, {}, lambda _n, _a: result)
    except TuniuFailure as exc:
        info["projection"] = exc.code
        return info
    info["projection"] = "SUCCESS"
    rooms = payload.get("roomTypes")
    nested = payload.get("data")
    info.update({
        "hotel_id_present": _positive_id(payload.get("hotelId")) is not None,
        "hotel_id_matches_search": (
            _positive_id(payload.get("hotelId")) == candidate.hotel_id
        ),
        "hotel_name_present": _text(payload.get("hotelName")) is not None,
        "hotel_name_matches_search": _text(payload.get("hotelName")) == candidate.name,
        "address_present": _text(payload.get("address")) is not None,
        "address_matches_search": _text(payload.get("address")) == candidate.address,
        "city_echo_present": _text(payload.get("cityName"), 40) is not None,
        "city_echo_matches_search": (
            _text(payload.get("cityName"), 40) == candidate.city
            if "cityName" in payload else None
        ),
        "check_in_echo_matches": (
            payload.get("checkIn") == scope.check_in
            if "checkIn" in payload else None
        ),
        "check_out_echo_matches": (
            payload.get("checkOut") == scope.check_out
            if "checkOut" in payload else None
        ),
        "adult_echo_matches": (
            type(payload.get("adultNum")) is int
            and payload["adultNum"] == scope.adults_per_room
            if "adultNum" in payload else None
        ),
        "room_count_echo_matches": (
            type(payload.get("roomNum")) is int
            and payload["roomNum"] == scope.rooms
            if "roomNum" in payload else None
        ),
        "room_types_list": isinstance(rooms, list),
        "room_types_count": min(len(rooms), 1000) if isinstance(rooms, list) else None,
        "nested_data_mapping": isinstance(nested, dict),
        "nested_data_room_types_list": isinstance(
            nested.get("roomTypes"), list
        ) if isinstance(nested, dict) else False,
        "room_rate_plan_list_count": sum(
            1 for room in rooms if isinstance(room, dict)
            and isinstance(room.get("ratePlans"), list)
        ) if isinstance(rooms, list) else None,
    })
    # Never walk arbitrary nested values, or format/show preBookParam.
    return info


def run(*, live: bool) -> dict:
    report = {
        "supplier": "tuniu",
        "live_requested": live,
        "transport_attempts": 0,
        "status": "UNCONFIGURED",
        "test_budget_yuan": 500,
        "product_budget_yuan": 200,
        "product_budget_passed": False,
    }
    try:
        mode, _ = auth_configuration()
    except TuniuConfigurationError as error:
        report["status"] = error.code
        return report
    if mode != "api_key":
        report["status"] = "AUTH_MODE_NOT_API_KEY"
        return report
    if not live:
        report["status"] = "LOCAL_CONFIGURED_NO_HTTP"
        return report

    raw_transport = make_transport(mode)
    attempts = 0
    search_called = False

    def limited_transport(body, headers, timeout):
        nonlocal attempts
        if attempts >= _MAX_ATTEMPTS:
            raise RollingGoError("REQUEST_LIMIT")
        try:
            request = json.loads(body)
            method = request.get("method")
            if method not in _ALLOWED:
                raise RollingGoError("TOOL_NOT_ALLOWED")
            if method == "tools/call":
                params = request.get("params")
                name = params.get("name") if isinstance(params, dict) else None
                if name not in (SEARCH_TOOL, DETAIL_TOOL):
                    raise RollingGoError("TOOL_NOT_ALLOWED")
                if name == DETAIL_TOOL and not search_called:
                    raise RollingGoError("TOOL_NOT_ALLOWED")
        except (ValueError, TypeError, AttributeError):
            raise RollingGoError("INVALID_ARGUMENTS") from None
        attempts += 1
        return raw_transport(body, headers, timeout)

    try:
        client = TuniuMCPClient(transport=limited_transport)
        available = {
            item["name"] for item in client._client.discover()
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        report["tools"] = {
            "search": SEARCH_TOOL in available,
            "detail": DETAIL_TOOL in available,
        }
        if not (SEARCH_TOOL in available and DETAIL_TOOL in available):
            report["status"] = "READ_TOOL_NOT_ADVERTISED"
            return report

        search = SearchInput(
            place="广州", place_type="城市",
            check_in=date(2026, 11, 13), check_out=date(2026, 11, 14),
            adults=2, rooms=1, children_ages=[], budget_fen=50000, limit=6,
        )
        report["conditions"] = {
            "city": "广州", "check_in": "2026-11-13",
            "check_out": "2026-11-14", "adults": 2, "rooms": 1,
        }
        diagnostic = {}

        def read_tool(name, arguments):
            nonlocal search_called
            if name == SEARCH_TOOL:
                search_called = True
            result = client.call_tool(name, arguments)
            if name == SEARCH_TOOL:
                payload = _source_call(name, arguments, lambda n, a: result)
                rows = payload.get("hotels")
                if isinstance(rows, list):
                    diagnostic.update({
                        "source_record_count": len(rows),
                        "valid_id_count": sum(
                            1 for x in rows if isinstance(x, dict)
                            and _positive_id(x.get("hotelId")) is not None
                        ),
                        "has_identity_count": sum(
                            1 for x in rows if isinstance(x, dict)
                            and _text(x.get("hotelName")) is not None
                            and _text(x.get("address")) is not None
                        ),
                        "independent_city_match_count": sum(
                            1 for x in rows if isinstance(x, dict)
                            and _text(x.get("cityName"), 40) == "广州"
                        ),
                        "city_field_present_count": sum(
                            1 for x in rows if isinstance(x, dict)
                            and _text(x.get("cityName"), 40) is not None
                        ),
                    })
                total_pages = payload.get("totalPageNum")
                diagnostic["more_pages_indicated"] = (
                    type(total_pages) is int and total_pages > 1
                )
                # No queryId, token, hotel ID/name/address or raw response stored.
            elif name == DETAIL_TOOL:
                # Only explicit whitelisted structural facts; never raw payload.
                report["detail_shape"] = safe_detail_shape(
                    result, candidate, found.scope,
                )
            return result

        provider = TuniuProvider(read_tool, mode="offline_test")
        found = provider.search(search, city="广州")
        report["search"] = {
            **diagnostic,
            "candidate_count": len(found.hotels),
            "excluded_count": found.excluded_count,
            "status": found.status,
            "limited": found.limited,
            "references_under_product_budget": sum(
                x.lowest_reference_fen is not None and x.lowest_reference_fen <= 20000
                for x in found.hotels
            ),
            "references_under_broader_budget": sum(
                x.lowest_reference_fen is not None and x.lowest_reference_fen <= 50000
                for x in found.hotels
            ),
        }
        if not found.hotels:
            report["status"] = "SEARCH_NO_VERIFIABLE_CANDIDATE"
            return report

        # Only the exact member of this search may be used for detail.
        candidates = [x for x in found.hotels if x.hotel_id < 2**31]
        if not candidates:
            report["status"] = "SOURCE_IDS_INCOMPATIBLE"
            return report
        candidate = min(
            candidates, key=lambda x: (
                x.lowest_reference_fen is None,
                x.lowest_reference_fen if x.lowest_reference_fen is not None else 10**12,
            ),
        )
        report["selected_hotel_reference_under_200"] = (
            candidate.lowest_reference_fen is not None
            and candidate.lowest_reference_fen <= 20000
        )
        if attempts >= _MAX_ATTEMPTS:
            report["status"] = "DETAIL_SKIPPED_RAW_BUDGET"
            return report
        detail = provider.detail(
            found, candidate, expected_request_id=found.request_id,
        )
        report["detail"] = {
            "quote_count": len(detail.quotes),
            "price_reference_count": sum(x.price_fen is not None for x in detail.quotes),
            "source_stock_positive_count": sum(
                x.remaining_count_observed is not None
                and x.remaining_count_observed > 0 for x in detail.quotes
            ),
            "observed_capacity_rejected_count": sum(
                x.capacity_rejected is True for x in detail.quotes
            ),
            "status": detail.status,
            "private_bathroom_verified": False,
            "all_mandatory_fees_verified": False,
            "booking_verified": False,
        }
        report["status"] = "DETAIL_REFERENCES_OBSERVED" if detail.quotes else "DETAIL_NO_RATES"
        return report
    except RollingGoError as error:
        report["status"] = error.code
        if error.status in (401, 403, 404, 429):
            report["http_status"] = error.status
        return report
    except (TuniuFailure, TuniuConfigurationError) as error:
        report["status"] = error.code
        return report
    except Exception:
        report["status"] = "UNEXPECTED_FAILURE"
        return report
    finally:
        report["transport_attempts"] = attempts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    arguments = parser.parse_args()
    result = run(live=arguments.live)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in (
        "LOCAL_CONFIGURED_NO_HTTP", "DETAIL_REFERENCES_OBSERVED",
        "DETAIL_NO_RATES", "SEARCH_NO_VERIFIABLE_CANDIDATE",
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
