"""One explicitly approved RollingGo search; report shapes, never raw values."""

# ruff: noqa: E402
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from scripts.evaluation.ledger import Budget
from scripts.evaluation.run import guard_http, write
from stay_agent.contracts import SearchInput
from stay_agent.providers.data.rollinggo import business_data, load_api_key
from stay_agent.providers.data.rollinggo_client import RollingGoClient
from stay_agent.service import QuoteService

FIELDS = (
    "city",
    "cityName",
    "cityCode",
    "cityname",
    "province",
    "destinationId",
    "areaCode",
    "address",
)
CITY_FIELDS = ("city", "cityName", "cityname")
LIMITS = {"rollinggo": 4}
QUERY = {
    "place": "广州",
    "place_type": "城市",
    "poi_name": "海底捞火锅(荔胜广场店)",
    "check_in": "2026-11-23",
    "check_out": "2026-11-24",
    "adults": 2,
    "rooms": 1,
    "children_ages": [],
    "budget_fen": 30000,
    "limit": 10,
    "country_region": "中国大陆",
}


def field_type(value):
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "unsupported"


def city_shape(data):
    records = data.get("hotelInformationList")
    valid = isinstance(records, list)
    rows = records[:10] if valid else []
    output = []
    for record in rows:
        if not isinstance(record, dict):
            output.append({"valid_record": False})
            continue
        fields = {}
        text_values = []
        for key in FIELDS:
            if key not in record:
                fields[key] = {"present": False}
                continue
            value = record[key]
            field = {
                "present": True,
                "type": field_type(value),
                "empty": value is None or value == "" or value == [] or value == {},
            }
            if key in CITY_FIELDS:
                text = value.strip() if isinstance(value, str) else ""
                field["lexical_class"] = (
                    "unknown"
                    if not text
                    else "requested_city_text"
                    if text in {"广州", "广州市"}
                    else "different_text"
                )
                if text:
                    text_values.append(text.removesuffix("市"))
            if key == "address":
                text = value.strip() if isinstance(value, str) else ""
                field["prefix_class"] = (
                    "city_prefix"
                    if text.startswith("广州市")
                    else "province_city_prefix"
                    if text.startswith("广东省广州市")
                    else "unknown"
                )
            fields[key] = field
        output.append(
            {
                "valid_record": True,
                "fields": fields,
                "city_text_fields_conflict": len(set(text_values)) > 1,
                "independent_city_verified": False,
            }
        )
    return {
        "valid_result_list": valid,
        "returned_count": len(records) if valid else None,
        "inspected_count": len(rows),
        "truncated": valid and len(records) > 10,
        "candidates": output,
    }


class ShapeClient:
    """Observe a bounded allowlist after redaction; do not retain the response."""

    def __init__(self, inner):
        self.inner = inner
        self.observation = None
        self.attempted = False

    def call_tool(self, name, arguments):
        if name != "searchHotels" or self.attempted:
            raise ValueError("ONE_SEARCH_ONLY")
        self.attempted = True
        result = self.inner.call_tool(name, arguments)
        self.observation = city_shape(business_data(self.inner.redact(result)))
        return result

    def redact(self, result):
        return self.inner.redact(result)


class NoSessionStore:
    """No source identifiers, names, URLs or response payloads persist here."""

    def save(self, *args, **kwargs):
        pass


def run(directory, budget):
    (directory / "probe.started").touch(exist_ok=False)
    summary = {
        "status": "started",
        "source": "rollinggo",
        "limit": 4,
        "raw_response_persisted": False,
        "source_identifiers_persisted": False,
    }
    try:
        with guard_http(budget):
            observed = ShapeClient(
                RollingGoClient(load_api_key(), timeout=20, total_timeout=60, max_requests=4)
            )
            response = QuoteService(lambda: observed).search(
                SearchInput.model_validate(QUERY), NoSessionStore()
            )
            summary.update(
                status=response.status,
                source_error=response.error.code if response.error else None,
                projected_hotel_count=len(response.hotels),
                shape=observed.observation,
            )
    except Exception as error:
        # Never print exception text, raw response or provider-supplied fields.
        code = getattr(error, "code", None)
        allowed = {
            "REQUEST_LIMIT",
            "AUTHENTICATION_FAILED",
            "ACCESS_DENIED",
            "RATE_LIMITED",
            "TIMEOUT",
            "MISSING_CREDENTIAL",
            "INVALID_CREDENTIAL",
            "CONFIGURATION_MISSING",
            "INVALID_RESPONSE",
            "RPC_ERROR",
            "TOOL_ERROR",
            "TRANSPORT_ERROR",
        }
        summary.update(status="stopped", source_error=code if code in allowed else "DRIVER_ERROR")
    finally:
        budget.stop("rollinggo")
        write(directory / "result.json", summary)
        write(directory / "budget-summary.json", budget.summary())
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print("BUDGET", json.dumps(budget.summary()), flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["init", "run"])
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    if not directory.is_relative_to(ROOT / "runtime"):
        raise ValueError("ISOLATED_RUNTIME_REQUIRED")
    if args.phase == "init":
        directory.mkdir(parents=True, exist_ok=False)
        Budget.create(directory / "budget.sqlite", LIMITS)
        write(
            directory / "manifest.json",
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "approved_limits": LIMITS,
                "query": QUERY,
                "baseline": "a0fc59a",
            },
        )
        print("INITIALIZED", directory)
        return
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("approved_limits") != LIMITS or manifest.get("query") != QUERY:
        raise ValueError("MANIFEST_MISMATCH")
    run(directory, Budget(directory / "budget.sqlite"))


if __name__ == "__main__":
    main()
