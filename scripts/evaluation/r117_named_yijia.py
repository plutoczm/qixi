"""Run one approved R117 named-hotel recall through the production service."""

# ruff: noqa: E402
import argparse
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from scripts.evaluation.ledger import Budget, BudgetExceeded
from scripts.evaluation.run import guard_http, write
from stay_agent.contracts import RecallInput, RecallResponse
from stay_agent.recall import RecallService
from stay_agent.storage import SessionStore

LIMITS = {"amap": 3, "rollinggo": 5}
RUN_DIR = ROOT / "runtime" / "r117-yijia-named-20261118"


def fixed_request() -> RecallInput:
    return RecallInput.model_validate({
        "operation_id": uuid4().hex,
        "strategy": "named_hotel",
        "provider": "amap",
        "query": {
            "place": "广州", "place_type": "城市", "country_region": "中国大陆",
            "check_in": "2026-11-23", "check_out": "2026-11-24",
            "adults": 2, "rooms": 1, "children_ages": [], "budget_fen": 30000,
        },
        "branch_limit": 3,
        "restaurant_keyword": "海底捞火锅(北京路店)",
        "restaurant_address": "中山四路395号5楼",
        "named_hotel": {
            "name": "怡佳公寓(北京路步行街公园前地铁站店)", "address": "中山五路昌兴街26号",
        },
    })


def project(response: RecallResponse) -> dict:
    coverage = response.recall_coverage
    return {
        "status": response.status,
        "source_error": response.error.code if response.error else None,
        "discovery_stop": coverage.discovery_stop_reason,
        "restaurant_pages": coverage.pages_requested,
        "hotel_map_lookups": coverage.named_hotel_map_lookups,
        "hotel_map_matches": coverage.named_hotel_map_matches,
        "hotel_supplier_lookups": coverage.hotel_lookups_attempted,
        "retained_hotels": len(response.hotels),
        "branches": [
            {"status": branch.status, "reason": branch.reason,
             "error": branch.error.code if branch.error else None}
            for branch in response.branches
        ],
        "targets": [
            {"status": target.status, "reason": target.reason, "match_status": target.match_status,
             "source_count": target.source_count}
            for branch in response.branches
            for target in branch.hotel_targets
        ],
        "reference_prices_fen": [hotel.reference_price_fen for hotel in response.hotels],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    request = fixed_request()
    if not args.live:
        print("PREFLIGHT_PASS: fixed request validated, no source calls")
        return 0
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    budget = Budget.create(RUN_DIR / "budget.sqlite", LIMITS)
    write(RUN_DIR / "manifest.json", {"limits": LIMITS, "request": request.model_dump(mode="json")})
    result = {"status": "driver_error"}
    try:
        with guard_http(budget), patch(
            "http.client.HTTPConnection.request", side_effect=BudgetExceeded("ENDPOINT_DENIED")
        ):
            service = RecallService()
            try:
                result = project(service.search(request, SessionStore(RUN_DIR / "search.sqlite3")))
            finally:
                service.close()
    except Exception:
        # The provider exception may include sensitive data.
        return 1
    finally:
        for source in LIMITS:
            budget.stop(source)
        write(RUN_DIR / "result.json", result)
        write(RUN_DIR / "budget-summary.json", budget.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
