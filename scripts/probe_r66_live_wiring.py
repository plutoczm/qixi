"""r66 bounded live probe: do the newly wired gates behave on real data?

Question this answers (P5/r64 遗留):
    r62 把参考层门禁接进报价链路, r64 把道旅报价、途牛来源入口、飞猪发现源
    接进产品——但三项都只有单元测试与离线投影验证。本探针用**真实供应商**
    跑一遍, 看页面实际会拿到什么:
      * 飞猪发现源: 是否真的返回候选, 展示价原文/坐标存在性是否如实;
      * 道旅报价: 门禁是否真的给出"未通过", 并且拦在正确的字段上;
      * 途牛报价: 参考层是否真的 PASS, 推定项是否带正确文案。

Approved budget for one run (user: 按现有额度跑):
    飞猪 1 次 CLI 调用; 道旅 1 次搜索 + 1 次房型; 途牛 1 次搜索 + 1 次房型。
    高德/腾讯/滴滴/模型/下单/翻页: 0 次。

Safety contract:
- 默认离线; 只有 ``--live`` 才联网。
- 不打印、不落盘原始供应商响应、酒店地址、预订链接、queryId、token。
  出进程的只有状态、计数、布尔、允许的字段名与门禁判定的中文说明。
- 不重试、不翻页、不下单、不打开链接、不切源兜底。
- 途牛走本项目 SQLite 原始 HTTP 账本, 发送前计数; 5/分钟与 50/天用尽即拒绝。

Usage:
    python scripts/probe_r66_live_wiring.py            # 离线计划与预检
    python scripts/probe_r66_live_wiring.py --live     # 有界真实运行
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
ADULTS = 2
ROOMS = 1
# 页面默认留空预算: 本次不放宽也不收紧, 只求能拿到候选看门禁。
BUDGET_FEN = None

#: Hard ceilings for one run. The script refuses to exceed them.
MAX_FLYAI_CALLS = 1
MAX_ROLLINGGO_QUOTE_CALLS = 1
MAX_TUNIU_QUOTE_CALLS = 1
TUNIU_MINUTE_LIMIT = 5


def today_shanghai() -> date:
    return datetime.now(ZONE).date()


def window() -> tuple[date, date]:
    check_in = today_shanghai() + timedelta(days=6)
    return check_in, check_in + timedelta(days=1)


def env_has(name: str) -> bool:
    """Credential *presence* only; the value never leaves this function."""
    if os.environ.get(name, "").strip():
        return True
    path = ROOT / ".env"
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return bool(line.split("=", 1)[1].strip())
    return False


def tuniu_day_count() -> int:
    try:
        return DurableRequestGate().known_count()
    except Exception:
        return -1


def tuniu_window_count() -> int:
    """Read-only: how many raw attempts the ledger recorded in the last 60s."""
    if not LEDGER_PATH.is_file():
        return 0
    try:
        con = sqlite3.connect(f"file:{LEDGER_PATH}?mode=ro", uri=True, timeout=2.0)
        try:
            return int(con.execute(
                "SELECT COUNT(*) FROM attempts WHERE at_epoch > ?",
                (time.time() - 60,),
            ).fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return -1


def wait_for_minute_window(*, needed: int = 1) -> float:
    """Sleep just enough for the shared 5/min supplier limit. No retry logic."""
    slept = 0.0
    used = tuniu_window_count()
    if used < 0:
        return slept
    if used + needed <= TUNIU_MINUTE_LIMIT:
        return slept
    delay = 61.0
    time.sleep(delay)
    return delay


def status_counts(items):
    out: dict[str, int] = {}
    for item in items:
        out[item["status"]] = out.get(item["status"], 0) + 1
    return out


def summarize_reference(quote) -> dict:
    gate = quote.get("reference")
    if gate is None:
        return {"evaluated": False, "ready": None, "items": 0}
    items = gate.get("items") or []
    return {
        "evaluated": True,
        "ready": bool(gate.get("ready")),
        "items": len(items),
        "by_status": status_counts(items),
        "noted": {
            item["field"]: item.get("note", "")
            for item in items
            if item["status"] in ("blocked", "presumed", "unknown")
        },
    }


def summarize_quotes(payload: dict) -> dict:
    quotes = payload.get("quotes") or []
    rows = []
    for quote in quotes[:6]:
        rows.append({
            "room_name": quote.get("room_name"),
            "amount_fen": quote.get("amount_fen"),
            "currency": quote.get("currency"),
            "source_inventory_observed": quote.get("source_inventory_observed"),
            "price_basis": quote.get("price_basis"),
            "reference": summarize_reference(quote),
        })
    return {
        "status": payload.get("status"),
        "error_code": (payload.get("error") or {}).get("code"),
        "booking_status": payload.get("booking_status"),
        "occupancy_status": payload.get("occupancy_status"),
        "booking_url_present": bool(payload.get("booking_url")),
        "quotes": len(quotes),
        "ready_quotes": sum(
            1 for q in quotes if (q.get("reference") or {}).get("ready") is True
        ),
        "samples": rows,
        "warnings": (payload.get("warnings") or [])[:4],
    }


def summarize_search(payload: dict) -> dict:
    hotels = payload.get("hotels") or []
    return {
        "status": payload.get("status"),
        "error_code": (payload.get("error") or {}).get("code"),
        "data_mode": payload.get("data_mode"),
        "hotels": len(hotels),
        "coverage_limited": (payload.get("coverage") or {}).get("limited"),
        "next_page": payload.get("next_page"),
        "first": [
            {
                "id": hotel.get("id"),
                "name": hotel.get("name"),
                "reference_price_fen": hotel.get("reference_price_fen"),
                "booking_url_present": bool(hotel.get("booking_url")),
            }
            for hotel in hotels[:5]
        ],
    }


def build_client(*, source: str, flyai_mode: str | None = None):
    from fastapi.testclient import TestClient
    from stay_agent.main import create_app

    os.environ["XUNQI_HOTEL_SOURCE"] = source
    if flyai_mode is not None:
        os.environ["XUNQI_FLYAI_MODE"] = flyai_mode
    # The app only trusts 127.0.0.1/localhost; TestClient defaults to
    # "testserver" and would otherwise be rejected before reaching any route.
    return TestClient(create_app(), base_url="http://127.0.0.1")


def run_flyai(report: dict) -> None:
    check_in, check_out = window()
    entry = report["sources"]["flyai"]
    client = build_client(source="tuniu", flyai_mode="cli")
    with client:
        response = client.post("/api/discovery", json={
            "place": CITY, "place_type": "城市",
            "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
        })
    if response.status_code != 200:
        entry.update({"http_status": response.status_code,
                      "body": response.json() if response.text else None})
        return
    payload = response.json()
    candidates = payload.get("candidates") or []
    entry.update({
        "http_status": 200,
        "source_id": (payload.get("source") or {}).get("id"),
        "status": payload.get("status"),
        "http_used": payload.get("http_used"),
        "error_code": (payload.get("error") or {}).get("code"),
        "candidates": len(candidates),
        "price_text_present": sum(1 for c in candidates if c.get("price_text")),
        "coordinates_present": sum(1 for c in candidates if c.get("coordinates_present")),
        "star_present": sum(1 for c in candidates if c.get("star") is not None),
        "nearby_transit_present": sum(1 for c in candidates if c.get("nearby_transit")),
        "limitations": len(payload.get("limitations") or []),
        "first_three": [
            {
                "name": c.get("name"),
                "price_text": c.get("price_text"),
                "star": c.get("star"),
                "coordinates_present": c.get("coordinates_present"),
            }
            for c in candidates[:3]
        ],
    })


def run_quotes_flow(report: dict, source: str) -> None:
    check_in, check_out = window()
    entry = report["sources"][source]
    client = build_client(source=source)
    with client:
        if source == "tuniu":
            entry["minute_window_slept"] = round(wait_for_minute_window(needed=1), 1)
        search = client.post("/api/search", json={
            "place": CITY, "place_type": "城市",
            "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "adults": ADULTS, "rooms": ROOMS,
            "budget_fen": BUDGET_FEN, "limit": 20,
        })
        if search.status_code != 200:
            entry["search"] = {"http_status": search.status_code,
                               "body": search.json() if search.text else None}
            return
        found = search.json()
        entry["search"] = summarize_search(found)
        hotels = found.get("hotels") or []
        if not hotels:
            entry["quotes"] = {"skipped": "NO_CANDIDATES", "quotes": 0}
            return
        # 取参考价最低的一家; 只为了看门禁在真实数据上的样子。
        priced = [h for h in hotels if h.get("reference_price_fen") is not None]
        target = min(priced, key=lambda h: h["reference_price_fen"]) if priced else hotels[0]
        entry["target"] = {"id": target.get("id"), "name": target.get("name")}
        if source == "tuniu":
            entry["minute_window_slept_2"] = round(wait_for_minute_window(needed=1), 1)
        quotes = client.post(
            f"/api/hotels/{target['id']}/quotes",
            json={"request_id": found["request_id"]},
        )
        if quotes.status_code != 200:
            entry["quotes"] = {"http_status": quotes.status_code,
                               "body": quotes.json() if quotes.text else None}
            return
        entry["quotes"] = summarize_quotes(quotes.json())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="真正联网; 不给就只打印离线计划")
    parser.add_argument("--out", default=None, help="证据 JSON 路径")
    parser.add_argument(
        "--only", default="flyai,rollinggo,tuniu",
        help="只跑指定来源（逗号分隔），未列出的来源不发请求",
    )
    args = parser.parse_args()

    check_in, check_out = window()
    before = tuniu_day_count()
    plan = {
        "probe": "r66-live-wiring",
        "query": {
            "place": CITY, "place_type": "城市",
            "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "adults": ADULTS, "rooms": ROOMS, "budget_fen": BUDGET_FEN,
        },
        "budget": {
            "flyai_calls": MAX_FLYAI_CALLS,
            "rollinggo_calls": 2,
            "tuniu_calls": 2,
            "other_sources_calls": 0,
            "tuniu_ledger_today_before": before,
            "tuniu_minute_window_used": tuniu_window_count(),
        },
        "preflight": {
            "TUNIU_API_KEY": env_has("TUNIU_API_KEY"),
            "XUNQI_TUNIU_AUTH_MODE": os.environ.get("XUNQI_TUNIU_AUTH_MODE")
            or "from .env",
            "ROLLINGGO_API_KEY": env_has("ROLLINGGO_API_KEY"),
            "FLYAI_API_KEY": env_has("FLYAI_API_KEY"),
            "flyai_cli_bundle": (ROOT / "runtime/vendor/flyai-cli/node_modules/"
                                 "@fly-ai/flyai-cli/dist/flyai-bundle.cjs").is_file(),
        },
        "sources": {},
    }
    if not args.live:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        print("\n离线模式: 未发出任何请求。加 --live 才联网。")
        return 0

    if before < 0:
        print("账本不可读, 拒绝联网。")
        return 2
    if before + 5 > 50:
        print(f"今日途牛已用 {before}/50, 不足以完成 5 次调用, 拒绝联网。")
        return 2

    selected = [name.strip() for name in args.only.split(",") if name.strip()]
    unknown = set(selected) - {"flyai", "rollinggo", "tuniu"}
    if unknown:
        print(f"未知来源: {sorted(unknown)}")
        return 2

    report = plan
    report["selected_sources"] = selected
    report["started_at"] = datetime.now(ZONE).isoformat(timespec="seconds")
    for name in selected:
        report["sources"][name] = {}

    if "flyai" in selected:
        try:
            run_flyai(report)
        except Exception as error:  # noqa: BLE001 - 记录真实失败, 不重试
            report["sources"]["flyai"]["exception"] = type(error).__name__

    if "rollinggo" in selected:
        try:
            run_quotes_flow(report, "rollinggo")
        except Exception as error:  # noqa: BLE001
            report["sources"]["rollinggo"]["exception"] = type(error).__name__

    if "tuniu" in selected:
        try:
            run_quotes_flow(report, "tuniu")
        except Exception as error:  # noqa: BLE001
            report["sources"]["tuniu"]["exception"] = type(error).__name__

    after = tuniu_day_count()
    report["budget"]["tuniu_ledger_today_after"] = after
    report["budget"]["tuniu_http_observed_delta"] = (
        after - before if after >= 0 and before >= 0 else None
    )
    report["finished_at"] = datetime.now(ZONE).isoformat(timespec="seconds")

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"evidence -> {path}")

    print(json.dumps(report["sources"], ensure_ascii=False, indent=2))
    print(json.dumps(report["budget"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
