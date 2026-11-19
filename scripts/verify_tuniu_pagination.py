"""One explicitly authorized Tuniu pagination check; default does ZERO HTTP.

--live makes at most 5 raw HTTPS attempts, inclusive of MCP initialization and
discovery. Never fetches detail, follows more than one page, retries, orders,
stores the opaque queryId, or prints source hotel identity/booking parameters.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.contracts import SearchInput  # noqa: E402
from stay_agent.providers.data.rollinggo_client import RollingGoError  # noqa: E402
from stay_agent.providers.data.tuniu import TuniuProvider, TuniuFailure  # noqa: E402
from stay_agent.providers.data.tuniu_mcp import (  # noqa: E402
    TuniuConfigurationError, TuniuMCPClient, auth_configuration, make_transport,
)


def verify(live=False):
    report = {
        "supplier": "tuniu", "live_requested": bool(live),
        "raw_http_attempts": 0,
        "technical_budget_yuan": 500, "product_budget_yuan": 200,
        "product_qualifications_verified": False,
        "status": "NOT_CONFIGURED",
    }
    try:
        mode, _ = auth_configuration()
        if mode != "api_key":
            report["status"] = "AUTH_MODE_NOT_API_KEY"
            return report
    except TuniuConfigurationError as exc:
        report["status"] = exc.code
        return report
    if not live:
        report["status"] = "CONFIGURED_NO_HTTP"
        return report
    attempts = 0
    send = make_transport(mode)

    def limited(body, headers, timeout):
        nonlocal attempts
        if attempts >= 5:
            raise RollingGoError("REQUEST_LIMIT")
        try:
            request = json.loads(body)
            method = request.get("method")
            if method not in (
                "initialize", "notifications/initialized", "tools/list", "tools/call",
            ):
                raise RollingGoError("TOOL_NOT_ALLOWED")
            if method == "tools/call":
                params = request.get("params")
                if not isinstance(params, dict) or params.get("name") != "tuniuHotelSearch":
                    raise RollingGoError("TOOL_NOT_ALLOWED")
        except (TypeError, ValueError, AttributeError):
            raise RollingGoError("INVALID_ARGUMENTS") from None
        attempts += 1  # failed transport counts too
        return send(body, headers, timeout)

    try:
        client = TuniuMCPClient(transport=limited)
        names = {tool.get("name") for tool in client._client.discover()
                 if isinstance(tool, dict)}
        if "tuniuHotelSearch" not in names:
            report["status"] = "SEARCH_TOOL_UNAVAILABLE"
            return report
        query = SearchInput(
            place="广州", place_type="城市",
            check_in=date(2026, 11, 13), check_out=date(2026, 11, 14),
            adults=2, rooms=1, children_ages=[],
            budget_fen=50000, limit=10,
        )
        projector = TuniuProvider(client.call_tool, mode="offline_test")
        first = projector.search(query, city=query.place)
        report["first"] = {
            "source_query_id_present": first.query_id is not None,
            "total_pages": min(first.total_pages, 100),
            "hotel_count": len(first.hotels),
            "product_budget_reference_count": sum(
                h.lowest_reference_fen is not None
                and h.lowest_reference_fen <= 20000 for h in first.hotels
            ),
            "limited": first.limited,
        }
        if not first.query_id or first.total_pages < 2:
            report["status"] = "NO_SAFE_SECOND_PAGE"
            return report
        if attempts >= 5:
            report["status"] = "RAW_HTTP_BUDGET_USED"
            return report
        second = projector.page(first, page_num=2)
        report["second"] = {
            "page": second.current_page,
            "hotel_count": len(second.hotels),
            "product_budget_reference_count": sum(
                h.lowest_reference_fen is not None
                and h.lowest_reference_fen <= 20000 for h in second.hotels
            ),
            "excluded_count": second.excluded_count,
            "limited": second.limited,
        }
        report["status"] = "PAGE_TWO_PROJECTION_OK"
        return report
    except (RollingGoError, TuniuFailure, TuniuConfigurationError) as exc:
        report["status"] = exc.code
        return report
    except Exception:
        report["status"] = "UNEXPECTED_FAILURE"
        return report
    finally:
        report["raw_http_attempts"] = attempts


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="At most five supplier raw HTTP calls.")
    args = parser.parse_args()
    result = verify(live=args.live)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in (
        "CONFIGURED_NO_HTTP", "PAGE_TWO_PROJECTION_OK", "NO_SAFE_SECOND_PAGE",
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
