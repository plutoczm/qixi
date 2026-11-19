"""r67 bounded live probe: close P5's open questions on real data.

Question this answers (P5 §8 补证清单 + 验收缺口):
    1. 可订层门禁接线后, 真实响应是否真的带着"未通过"的判定与逐项理由?
    2. 门禁在三类真实条件下是否都说得对 —— 单晚单房、多晚、两房?
       (单测覆盖了这些反例, 但真实来源从未跑过。)
    3. r59 发现的详情顶层 ``policies`` 九个键从未读过。押金／到店必付是
       可订层唯一还可能有新证据的字段, 这里只看**形状**: 键名、类型、数量、
       是否存在押金语义的键。值一律不出进程。

Approved budget for one run: 3 次业务查询 (每次 1 搜索 + 1 详情), 每次含
3 条 MCP 握手 = 最多 15 原始 HTTP。高德/腾讯/滴滴/飞猪/模型/下单/翻页: 0。

Safety contract:
- 默认离线; 只有 ``--live`` 才联网。
- 不打印、不落盘原始响应、酒店名址、预订链接、queryId、token、preBookParam。
  出进程的只有状态、计数、布尔、允许的结构化键名与门禁中文说明。
- 不重试、不翻页、不下单、不打开链接、不切源兜底。
- 途牛走本项目 SQLite 原始 HTTP 账本, 发送前计数; 5/分钟与 50/天用尽即拒绝。

Usage:
    python scripts/probe_r67_p5_closure.py            # 离线计划与预检
    python scripts/probe_r67_p5_closure.py --live     # 有界真实运行
    python scripts/probe_r67_p5_closure.py --live --only base
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.providers.data.tuniu_budget import (  # noqa: E402
    LEDGER_PATH,
    ZONE,
    DurableRequestGate,
)

CITY = "广州"
ADULTS_PER_ROOM = 2
BUDGET_FEN = None  # 页面默认留空: 不放宽也不收紧, 只求拿到候选看门禁。

#: Hard ceilings for one run.
MAX_HTTP_PER_CASE = 5
CASES: dict[str, dict] = {
    "base": {"nights": 1, "rooms": 1, "label": "单晚单房（基线）"},
    "three_nights": {"nights": 3, "rooms": 1, "label": "三晚单房（逐夜缺口）"},
    "two_rooms": {"nights": 1, "rooms": 2, "label": "单晚两房（房量缺口）"},
}
TUNIU_MINUTE_LIMIT = 5

#: Key names may be structural identifiers only. Nothing else leaves the process.
DEPOSIT_TOKENS = (
    "deposit", "押金", "guarantee", "prepay", "prepay", "cash", "incident",
    "fee", "charge", "surcharge", "tax",
)


def today_shanghai() -> date:
    return datetime.now(ZONE).date()


def check_in_date() -> date:
    return today_shanghai() + timedelta(days=6)


def ledger_day_count() -> int:
    try:
        return DurableRequestGate().known_count()
    except Exception:
        return -1


def ledger_window_count() -> int:
    """Read-only: raw attempts the ledger recorded in the last 60 seconds."""
    if not LEDGER_PATH.is_file():
        return 0
    try:
        con = sqlite3.connect(f"file:{LEDGER_PATH}?mode=ro", uri=True, timeout=2.0)
        try:
            return int(con.execute(
                "SELECT COUNT(*) FROM attempts WHERE at_epoch > ?", (time.time() - 60,),
            ).fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return -1


def wait_for_minute_window(*, needed: int = 1) -> float:
    """Sleep just enough for the shared 5/min supplier limit. No retry logic."""
    used = ledger_window_count()
    if used < 0 or used + needed <= TUNIU_MINUTE_LIMIT:
        return 0.0
    time.sleep(61.0)
    return 61.0


# --- safe shape projection ----------------------------------------------------

def _shape(value) -> dict:
    """Type name and container size only; never the value itself."""
    out: dict = {"type": type(value).__name__}
    if isinstance(value, (list, tuple, dict)):
        out["count"] = len(value)
    if type(value) is bool:
        out["boolean"] = value
    return out


def _key_names(value, limit: int = 30) -> list[str]:
    if not isinstance(value, dict):
        return []
    names = sorted(str(name)[:40] for name in value if str(name).isprintable())
    return names[:limit]


def _deposit_like(names) -> list[str]:
    out = []
    for name in names:
        low = str(name).lower()
        if any(token in low for token in DEPOSIT_TOKENS):
            out.append(str(name)[:40])
    return out[:20]


def detail_payload(envelope) -> dict | None:
    """Unwrap a read-tool envelope without ever keeping the raw text.

    Mirrors the adapter's rule: business markers in ``structuredContent``, or a
    single ``content[0].text`` JSON string.
    """
    if not isinstance(envelope, dict):
        return None
    structured = envelope.get("structuredContent")
    if isinstance(structured, dict) and structured:
        return structured
    blocks = envelope.get("content")
    if isinstance(blocks, list) and blocks:
        text = blocks[0].get("text") if isinstance(blocks[0], dict) else None
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def describe_policies(result) -> dict:
    """Shape of the detail's top-level ``policies``; no values, no free text.

    ``_source_call`` already unwraps the MCP envelope, so the argument is
    normally the business payload itself. Envelopes are still accepted, because
    a future adapter may hand back the raw shape.
    """
    payload = result if isinstance(result, dict) else None
    if payload is not None and (
        "structuredContent" in payload
        or ("content" in payload and not any(
            marker in payload for marker in ("roomTypes", "hotelId", "hotelName")
        ))
    ):
        payload = detail_payload(payload)
    if payload is None:
        return {"payload_parsed": False}
    top = _key_names(payload, limit=40)
    policies = payload.get("policies")
    out: dict = {
        "payload_parsed": True,
        "top_level_key_count": len(payload),
        "top_level_keys": top,
        "policies_present": policies is not None,
    }
    if policies is None:
        return out
    out["policies_shape"] = _shape(policies)
    names = _key_names(policies, limit=30)
    out["policy_keys"] = names
    out["policy_deposit_like_keys"] = _deposit_like(names)
    # One level deeper, so a nested deposit block is visible by name only.
    nested: dict[str, list[str]] = {}
    if isinstance(policies, dict):
        for name, value in list(policies.items())[:12]:
            if isinstance(value, dict):
                nested[str(name)[:30]] = _key_names(value, limit=12)
            elif isinstance(value, list):
                first = next((item for item in value if isinstance(item, dict)), None)
                if first is not None:
                    nested[str(name)[:30]] = _key_names(first, limit=12)
    out["policy_nested_keys"] = nested
    out["policy_nested_deposit_like"] = {
        parent: _deposit_like(children) for parent, children in nested.items()
        if _deposit_like(children)
    }
    return out


# --- payload summaries --------------------------------------------------------

def _status_counts(items) -> dict:
    out: dict[str, int] = {}
    for item in items:
        out[item["status"]] = out.get(item["status"], 0) + 1
    return out


def summarize_bookability(payload: dict) -> dict:
    gate = payload.get("bookability")
    if gate is None:
        return {"evaluated": False, "note": "本次未评估, 页面须显示未评估而非通过"}
    items = gate.get("items") or []
    return {
        "evaluated": True,
        "ready": bool(gate.get("ready")),
        "items": len(items),
        "by_status": _status_counts(items),
        "blocked_labels": [i["label"] for i in items if i["status"] == "blocked"],
        "verified_labels": [i["label"] for i in items if i["status"] == "verified"],
        "notes": {i["field"]: i.get("note", "") for i in items},
    }


def summarize_reference(quotes) -> dict:
    evaluated = [q for q in quotes if q.get("reference") is not None]
    ready = [q for q in evaluated if q["reference"].get("ready") is True]
    blocked_fields: dict[str, int] = {}
    presumed_fields: dict[str, int] = {}
    for quote in evaluated:
        for item in quote["reference"].get("items") or []:
            target = blocked_fields if item["status"] == "blocked" else None
            if item["status"] == "blocked":
                target = blocked_fields
            elif item["status"] == "presumed":
                target = presumed_fields
            if target is not None:
                target[item["label"]] = target.get(item["label"], 0) + 1
    return {
        "quotes": len(quotes),
        "evaluated": len(evaluated),
        "ready": len(ready),
        "blocked_fields": blocked_fields,
        "presumed_fields": presumed_fields,
    }


def summarize_quotes(payload: dict) -> dict:
    quotes = payload.get("quotes") or []
    return {
        "http_status": 200,
        "status": payload.get("status"),
        "error_code": (payload.get("error") or {}).get("code"),
        "booking_status": payload.get("booking_status"),
        "occupancy_status": payload.get("occupancy_status"),
        "booking_url_present": bool(payload.get("booking_url")),
        "reference": summarize_reference(quotes),
        "bookability": summarize_bookability(payload),
        "inventory_observed_present": sum(
            1 for q in quotes if q.get("source_inventory_observed") is not None
        ),
        "inventory_below_rooms": sum(
            1 for q in quotes
            if q.get("source_inventory_observed") is not None
            and q["source_inventory_observed"] < (payload.get("context") or {}).get("rooms", 1)
        ),
        "warnings": (payload.get("warnings") or [])[:4],
    }


def summarize_search(payload: dict) -> dict:
    hotels = payload.get("hotels") or []
    return {
        "http_status": 200,
        "status": payload.get("status"),
        "error_code": (payload.get("error") or {}).get("code"),
        "hotels": len(hotels),
        "coverage_limited": (payload.get("coverage") or {}).get("limited"),
        "next_page": payload.get("next_page"),
        "priced_hotels": sum(1 for h in hotels if h.get("reference_price_fen") is not None),
    }


# --- run ----------------------------------------------------------------------

def build_client(source: str):
    from fastapi.testclient import TestClient
    from stay_agent.main import create_app

    os.environ["XUNQI_HOTEL_SOURCE"] = source
    # The app only trusts 127.0.0.1/localhost; TestClient defaults to
    # "testserver" and would otherwise be rejected before reaching any route.
    return TestClient(create_app(), base_url="http://127.0.0.1")


def install_policy_spy(store: list[dict]):
    """Observe detail envelopes in-process; never alter or store them."""
    import stay_agent.providers.data.tuniu as tuniu_mod

    original = tuniu_mod._source_call

    def spy(tool, arguments, callback):
        result = original(tool, arguments, callback)
        if tool == tuniu_mod.DETAIL_TOOL:
            store.append(describe_policies(result))
        return result

    tuniu_mod._source_call = spy
    return lambda: setattr(tuniu_mod, "_source_call", original)


def run_case(report: dict, name: str) -> None:
    case = CASES[name]
    check_in = check_in_date()
    check_out = check_in + timedelta(days=case["nights"])
    entry: dict = {
        "label": case["label"],
        "query": {
            "nights": case["nights"], "rooms": case["rooms"],
            "adults_per_room": ADULTS_PER_ROOM, "budget_fen": BUDGET_FEN,
        },
        "http_budget": MAX_HTTP_PER_CASE,
    }
    report["cases"][name] = entry
    shapes: list[dict] = []
    restore = install_policy_spy(shapes)
    client = build_client("tuniu")
    try:
        with client:
            entry["minute_window_slept"] = round(wait_for_minute_window(needed=1), 1)
            search = client.post("/api/search", json={
                "place": CITY, "place_type": "城市",
                "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
                "adults": ADULTS_PER_ROOM, "rooms": case["rooms"],
                "children_ages": [], "budget_fen": BUDGET_FEN, "limit": 20,
            })
            if search.status_code != 200:
                entry["search"] = {"http_status": search.status_code}
                return
            found = search.json()
            entry["search"] = summarize_search(found)
            hotels = found.get("hotels") or []
            if not hotels:
                entry["quotes"] = {"skipped": "NO_CANDIDATES"}
                return
            priced = [h for h in hotels if h.get("reference_price_fen") is not None]
            target = min(priced, key=lambda h: h["reference_price_fen"]) if priced else hotels[0]
            entry["target"] = {"masked": True, "reference_price_fen":
                               target.get("reference_price_fen")}
            entry["minute_window_slept_2"] = round(wait_for_minute_window(needed=1), 1)
            quotes = client.post(
                f"/api/hotels/{target['id']}/quotes",
                json={"request_id": found["request_id"]},
            )
            if quotes.status_code != 200:
                entry["quotes"] = {"http_status": quotes.status_code}
                return
            entry["quotes"] = summarize_quotes(quotes.json())
    finally:
        restore()
        entry["detail_shapes"] = shapes[:1]  # exactly one detail per case


def selftest() -> int:
    """Offline: the shape reader must work on every envelope the adapter sees.

    r67 first run returned ``payload_parsed: false`` for all three cases because
    ``_source_call`` had already unwrapped the payload and this reader kept
    looking for an envelope. A silent "no data" is worse than a crash, so the
    three shapes are pinned here.
    """
    business = {
        "hotelId": 9, "hotelName": "某酒店", "address": "某地址",
        "roomTypes": [{"rtId": 1, "roomName": "某房型"}],
        "policies": {
            "checkInPolicy": {"text": "14:00 后"},
            "depositPolicy": {"amount": 200, "currency": "CNY"},
            "notice": ["某说明"],
        },
    }
    stripped = {key: value for key, value in business.items() if key != "policies"}
    cases = {
        "unwrapped": business,
        "structured_content": {"structuredContent": business},
        "content_text": {"content": [{"type": "text", "text": json.dumps(business)}]},
        "no_policies": stripped,
        "not_a_mapping": ["no"],
    }
    out: dict[str, dict] = {}
    for name, value in cases.items():
        shape = describe_policies(value)
        out[name] = {
            "parsed": shape.get("payload_parsed"),
            "policies_present": shape.get("policies_present"),
            "policy_keys": shape.get("policy_keys"),
            "deposit_like": shape.get("policy_deposit_like_keys"),
        }
    expected = {
        "unwrapped": (True, True),
        "structured_content": (True, True),
        "content_text": (True, True),
        "no_policies": (True, False),
        "not_a_mapping": (False, None),
    }
    failures = [
        name for name, want in expected.items()
        if (out[name]["parsed"], out[name]["policies_present"]) != want
    ]
    assert out["unwrapped"]["deposit_like"] == ["depositPolicy"], out["unwrapped"]
    assert "amount" not in json.dumps(out), "values must never leave the reader"
    print(json.dumps({"selftest": "ok" if not failures else "failed",
                      "failures": failures, "cases": out}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="真正联网; 不给就只打印离线计划")
    parser.add_argument("--selftest", action="store_true",
                        help="只跑形状读取器的离线自检, 不联网")
    parser.add_argument("--out", default=None, help="证据 JSON 路径")
    parser.add_argument("--only", default=",".join(CASES),
                        help=f"只跑指定用例（逗号分隔）: {','.join(CASES)}")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    names = [name.strip() for name in args.only.split(",") if name.strip()]
    unknown = [name for name in names if name not in CASES]
    if unknown:
        print(json.dumps({"error": "UNKNOWN_CASE", "cases": unknown}, ensure_ascii=False))
        return 2

    before = ledger_day_count()
    needed = MAX_HTTP_PER_CASE * len(names)
    report: dict = {
        "probe": "r67-p5-closure",
        "live": bool(args.live),
        "city": CITY,
        "check_in": check_in_date().isoformat(),
        "cases_planned": names,
        "http_ceiling": needed,
        "ledger_before": before,
        "cases": {},
    }
    if not args.live:
        report["plan"] = {
            "per_case": "1 搜索 + 1 详情 + 3 握手 = 5 原始 HTTP",
            "detail_of_each_case": "顺带记录详情顶层 policies 形状（押金线索），"
                                   "不额外消耗 HTTP",
            "not_called": ["高德", "腾讯", "滴滴", "飞猪", "模型", "下单", "翻页"],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if before < 0:
        print(json.dumps({"error": "LEDGER_UNAVAILABLE"}, ensure_ascii=False))
        return 3
    if before + needed > 50:
        print(json.dumps({
            "error": "BUDGET_REFUSED", "ledger_before": before, "needed": needed,
            "daily_limit": 50,
        }, ensure_ascii=False))
        return 4

    for name in names:
        run_case(report, name)
        print(f"[r67] {name} done", file=sys.stderr)

    report["ledger_after"] = ledger_day_count()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"written": str(path), "ledger_after": report["ledger_after"]},
                         ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
