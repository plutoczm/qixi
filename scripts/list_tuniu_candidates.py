"""One-shot, bounded live listing of Tuniu search candidates and room types.

User-approved budget: 3 MCP handshake + 1 search + at most 6 details = 10 raw
HTTP. Details are paced client-side to respect the shared 5 RPM limit. The
script never paginates, retries, books, opens links, or prints raw supplier
data / preBookParam / queryId / credentials.

Output includes public hotel name/address for the user's review and must stay
in the gitignored runtime directory, outside project documentation.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import date, datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.contracts import SearchInput  # noqa: E402
from stay_agent.providers.data.rollinggo_client import RollingGoError  # noqa: E402
from stay_agent.providers.data.tuniu import (  # noqa: E402
    DETAIL_TOOL, SEARCH_TOOL, TuniuFailure, TuniuProvider,
)
from stay_agent.providers.data.tuniu_mcp import (  # noqa: E402
    TuniuConfigurationError, TuniuMCPClient, auth_configuration, make_transport,
)

MAX_ATTEMPTS = 10
MAX_DETAILS = 6
ALLOWED = frozenset(("initialize", "notifications/initialized", "tools/list", "tools/call"))
WINDOW = 60.0
SEND_TIMES: deque[float] = deque()


def _pace() -> None:
    """Keep at most 5 sends in any rolling minute, outside any request deadline."""
    while True:
        now = time.monotonic()
        while SEND_TIMES and SEND_TIMES[0] <= now - WINDOW:
            SEND_TIMES.popleft()
        if len(SEND_TIMES) < 5:
            return
        time.sleep(max(0.2, SEND_TIMES[0] + WINDOW + 0.5 - now))


def _hotel_row(hotel) -> dict:
    return {
        "hotel_id": hotel.hotel_id,
        "name": hotel.name,
        "address": hotel.address,
        "city": hotel.city,
        "star": hotel.star_label,
        "search_lowest_fen": hotel.lowest_reference_fen,
    }


def _summarize_detail(detail) -> dict:
    quotes = detail.quotes
    prices_all = [q.price_fen for q in quotes if q.price_fen is not None]
    eligible = [
        q for q in quotes
        if q.price_fen is not None and q.capacity_rejected is not True
    ]
    eligible_prices = [q.price_fen for q in eligible]
    return {
        "status": detail.status,
        "quote_count": len(quotes),
        "room_type_count": len({q.room_type_id for q in quotes}),
        "room_type_names": sorted({q.room_name for q in quotes}),
        "price_reference_count": len(prices_all),
        "min_price_all_fen": min(prices_all) if prices_all else None,
        "min_price_eligible_fen": min(eligible_prices) if eligible_prices else None,
        "eligible_quote_count": len(eligible),
        "capacity_rejected_count": sum(q.capacity_rejected is True for q in quotes),
        "capacity_unknown_count": sum(
            q.capacity_rejected is None for q in quotes
        ),
        "source_stock_positive_count": sum(
            q.remaining_count_observed is not None and q.remaining_count_observed > 0
            for q in quotes
        ),
        "occupancy_verified": False,
        "private_bathroom_verified": False,
        "mandatory_fees_verified": False,
    }


def run(*, live: bool) -> dict:
    report = {
        "supplier": "tuniu",
        "live_requested": live,
        "transport_attempts": 0,
        "max_attempts": MAX_ATTEMPTS,
        "status": "UNCONFIGURED",
        "hotels": [],
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
        if attempts >= MAX_ATTEMPTS:
            raise RollingGoError("REQUEST_LIMIT")
        try:
            request = json.loads(body)
            method = request.get("method")
            if method not in ALLOWED:
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
        SEND_TIMES.append(time.monotonic())
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
            "source_budget_yuan": 500, "product_budget_yuan": 200,
        }
        report["generated_at"] = datetime.now().astimezone().isoformat()

        def read_tool(name, arguments):
            nonlocal search_called
            if name == SEARCH_TOOL:
                search_called = True
            return client.call_tool(name, arguments)

        provider = TuniuProvider(read_tool, mode="offline_test")
        found = provider.search(search, city="广州")
        report["search"] = {
            "candidate_count": len(found.hotels),
            "status": found.status,
            "limited": found.limited,
            "excluded_count": found.excluded_count,
        }
        if not found.hotels:
            report["status"] = "SEARCH_NO_VERIFIABLE_CANDIDATE"
            return report

        candidates = [x for x in found.hotels if x.hotel_id < 2**31]
        candidates.sort(key=lambda x: (
            x.lowest_reference_fen is None,
            x.lowest_reference_fen if x.lowest_reference_fen is not None else 10**12,
        ))
        report["hotels"] = [_hotel_row(x) for x in candidates]
        detail_used = 0
        stopped_on = None
        for index, hotel in enumerate(candidates):
            if detail_used >= MAX_DETAILS:
                break
            _pace()
            try:
                detail = provider.detail(
                    found, hotel, expected_request_id=found.request_id,
                )
            except (TuniuFailure, RollingGoError) as error:
                code = getattr(error, "code", "SOURCE_ERROR")
                report["hotels"][index]["detail"] = {"status": code}
                stopped_on = code
                for later in range(index + 1, len(candidates)):
                    report["hotels"][later]["detail"] = {"status": "NOT_REQUESTED"}
                break
            detail_used += 1
            report["hotels"][index]["detail"] = _summarize_detail(detail)

        if any("detail" in row for row in report["hotels"]):
            report["status"] = "CANDIDATES_LISTED"
        else:
            report["status"] = "SEARCH_ONLY"
        if stopped_on:
            report["detail_stopped_on"] = stopped_on
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
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in ("CANDIDATES_LISTED", "SEARCH_ONLY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
