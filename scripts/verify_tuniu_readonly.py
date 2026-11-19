"""Bounded, opt-in Tuniu hotel MCP discovery and read-only source smoke.

Default invocation performs *no* provider HTTP. --live requires an explicit
operator decision and allows at most four raw HTTP attempts in this process:
initialize, notification, tools/list and optionally one search. No retries,
orders, payment, source raw response, credential or booking token is printed.
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
from stay_agent.providers.data.tuniu import TuniuFailure, TuniuProvider  # noqa: E402
from stay_agent.providers.data.tuniu_mcp import (  # noqa: E402
    TuniuConfigurationError,
    TuniuMCPClient,
    auth_configuration,
    make_transport,
)

_MAX_RAW = 4
_ALLOWED_METHODS = frozenset(
    ("initialize", "notifications/initialized", "tools/list", "tools/call")
)
_ALLOWED_SEARCH = "tuniuHotelSearch"
_ALLOWED_DETAIL = "tuniuHotelDetail"


def run(*, live: bool, search: bool) -> dict:
    outcome = {
        "provider": "tuniu",
        "mode": "api_key",
        "live_requested": live,
        "search_requested": search,
        "raw_http_attempts": 0,
        "status": "UNCONFIGURED",
    }
    try:
        mode, _ = auth_configuration()
        outcome["mode"] = mode
    except TuniuConfigurationError as exc:
        outcome["status"] = exc.code
        return outcome

    if mode != "api_key":
        outcome["status"] = "AUTH_MODE_NOT_API_KEY"
        return outcome
    if not live:
        outcome["status"] = "LOCAL_CONFIGURED_NO_HTTP"
        return outcome

    raw_transport = make_transport(mode)
    attempts = 0

    def bounded(body: bytes, headers: dict, timeout: float):
        nonlocal attempts
        try:
            payload = json.loads(body)
            method = payload.get("method")
            if method not in _ALLOWED_METHODS:
                raise RollingGoError("TOOL_NOT_ALLOWED")
            if method == "tools/call" and (
                not search
                or payload.get("params", {}).get("name") != _ALLOWED_SEARCH
            ):
                raise RollingGoError("TOOL_NOT_ALLOWED")
            if attempts >= _MAX_RAW:
                raise RollingGoError("REQUEST_LIMIT")
        except (ValueError, TypeError, AttributeError):
            raise RollingGoError("INVALID_ARGUMENTS") from None
        attempts += 1  # includes failed connection / 401 / timeout attempts
        return raw_transport(body, headers, timeout)

    try:
        client = TuniuMCPClient(transport=bounded)
        tools = client._client.discover()
        names = {item["name"] for item in tools}
        outcome["discovery"] = {
            "read_tools": sorted(names & {_ALLOWED_SEARCH, _ALLOWED_DETAIL}),
            "hotel_search_available": _ALLOWED_SEARCH in names,
            "hotel_detail_available": _ALLOWED_DETAIL in names,
        }
        if _ALLOWED_SEARCH not in names:
            outcome["status"] = "HOTEL_SEARCH_TOOL_NOT_ADVERTISED"
            return outcome
        if not search:
            outcome["status"] = "DISCOVERY_OK_NO_BUSINESS_HTTP"
            return outcome

        query = SearchInput(
            place="广州", place_type="城市",
            check_in=date(2026, 11, 13), check_out=date(2026, 11, 14),
            adults=2, rooms=1, children_ages=[],
            budget_fen=20000, limit=6,
        )
        evidence = TuniuProvider(
            client.call_tool, mode="offline_test"
        ).search(query, city="广州")
        # Do not print actual hotel names, addresses, price values or raw source.
        outcome["search"] = {
            "city": "广州",
            "check_in": query.check_in.isoformat(),
            "check_out": query.check_out.isoformat(),
            "adults": 2, "rooms": 1,
            "candidate_count": len(evidence.hotels),
            "price_reference_count": sum(
                item.lowest_reference_fen is not None for item in evidence.hotels
            ),
            "status": evidence.status,
            "source_identity_exclusions": evidence.excluded_count,
            "limited": evidence.limited,
            "booking_verified": False,
        }
        outcome["status"] = "SEARCH_PROJECTION_OK_NOT_BOOKABILITY"
        return outcome
    except RollingGoError as exc:
        outcome["status"] = exc.code
        if exc.status in (401, 403, 404, 429):
            outcome["http_status"] = exc.status
        return outcome
    except (TuniuFailure, TuniuConfigurationError) as exc:
        outcome["status"] = exc.code
        return outcome
    except Exception:
        # Raw transport/provider messages may carry credentials or sensitive IDs.
        outcome["status"] = "UNEXPECTED_FAILURE"
        return outcome
    finally:
        outcome["raw_http_attempts"] = attempts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true",
        help="Explicitly authorize at most 4 raw hotel-MCP HTTP attempts.",
    )
    parser.add_argument(
        "--search", action="store_true",
        help="Include one bounded, read-only Guangzhou hotel search.",
    )
    arguments = parser.parse_args()
    if arguments.search and not arguments.live:
        parser.error("--search requires --live")
    report = run(live=arguments.live, search=arguments.search)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] in (
        "LOCAL_CONFIGURED_NO_HTTP",
        "DISCOVERY_OK_NO_BUSINESS_HTTP",
        "SEARCH_PROJECTION_OK_NOT_BOOKABILITY",
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
