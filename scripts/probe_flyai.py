"""Bounded, read-only FlyAI hotel-search probe (2 calls max).

Uses the same `FlyAICliClient` as the application adapters: the key travels
through the child environment only, raw payloads stay under the git-ignored
runtime directory, and the console prints a sanitized summary. No detail,
booking, retry or order call is made.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.providers.data.flyai_client import (  # noqa: E402
    FlyAICliClient, FlyAIError, load_key,
)

OUT_DIR = ROOT / "runtime/tmp-docs"
MAX_CALLS = 2


def summarize(payload) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("itemList") if isinstance(data, dict) else None
    rows = rows if isinstance(rows, list) else []
    prices = []
    with_price = 0
    with_jump = 0
    safe_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = row.get("price")
        if isinstance(price, (int, float)):
            with_price += 1
            prices.append(float(price))
        elif isinstance(price, str) and price.startswith("¥"):
            with_price += 1
            try:
                prices.append(float(price[1:]))
            except ValueError:
                pass
        if row.get("jumpUrl"):
            with_jump += 1
        safe_rows.append({
            "name": row.get("name"),
            "address": row.get("address"),
            "cityName": row.get("cityName"),
            "price": price,
            "currency": row.get("currency"),
            "has_jump_url": bool(row.get("jumpUrl")),
        })
    return {
        "top_level_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "items": len(rows),
        "with_price": with_price,
        "with_jump_url": with_jump,
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
        "rows": safe_rows[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poi", default=None, help="optional nearby POI anchor")
    parser.add_argument("--dest", default="广州")
    parser.add_argument("--check-in", default="2026-11-13")
    parser.add_argument("--check-out", default="2026-11-14")
    parser.add_argument("--max-price", type=int, default=200)
    parser.add_argument("--out", default="flyai-probe")
    args = parser.parse_args()

    key = load_key()
    if not key:
        print("FLYAI_API_KEY not configured", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = FlyAICliClient(key, max_requests=MAX_CALLS, timeout=90)
    arguments = {
        "destName": args.dest, "checkInDate": args.check_in,
        "checkOutDate": args.check_out, "maxPrice": args.max_price,
    }
    calls = [dict(arguments)]
    if args.poi:
        calls.append({**arguments, "poiName": args.poi, "sort": "distance_asc"})
    calls = calls[:MAX_CALLS]

    reports = []
    for index, call in enumerate(calls, start=1):
        try:
            payload = client.call_tool("search_hotels", call)
        except FlyAIError as error:
            summary = {"call": index, "error": error.code}
            print(json.dumps(summary, ensure_ascii=False))
            reports.append(summary)
            continue
        raw_path = OUT_DIR / f"{args.out}-{index}.json"
        raw_path.write_text(
            json.dumps({"arguments": call, "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
        summary = summarize(payload)
        summary.update({"call": index, "raw": raw_path.name})
        reports.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
