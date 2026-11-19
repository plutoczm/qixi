"""Run the one approved R120 search and one optional room lookup in Edge."""

# ruff: noqa: E402
import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from fastapi.responses import JSONResponse
import uvicorn
from scripts.evaluation import run
from scripts.evaluation.ledger import Budget, BudgetExceeded
from stay_agent.contracts import SearchInput
from stay_agent.conversation import ConversationService
from stay_agent.conversation_store import ConversationStore
from stay_agent.flyai_source import parallel_flyai_adapter
from stay_agent.multi_source import SourceContext
from stay_agent.providers.data.flyai import FlyAIDiscoveryProvider
from stay_agent.providers.data.flyai_client import FlyAICliClient, load_key, search_callback
from stay_agent.providers.data.tuniu import TuniuFailure
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore

DAY = "2026-11-18"
LIMITS = {"rollinggo": 10, "tuniu": 6, "flyai": 1}
DEST_LIMITS = {"rollinggo": 10, "tuniu": 10, "flyai": 2, "amap": 3}
HOSTS = {"mcp.rollinggo.cn": "rollinggo", "openapi.tuniu.cn": "tuniu",
         "flyai.open.fliggy.com": "flyai", "restapi.amap.com": "amap"}
DIRECTORY = ROOT / "runtime" / "r120-three-source-20261118"
ANCHOR = {"name": "海底捞火锅(北京路店)", "city": "广州市",
          "address": "中山四路395号5楼(公园前地铁站出入口步行220米)"}
QUERY = {"place": "广州", "place_type": "城市", "country_region": "中国大陆",
         "check_in": "2026-11-23", "check_out": "2026-11-24", "adults": 2,
         "rooms": 1, "children_ages": [], "budget_fen": 30000, "limit": 20}


def build(directory, expected, destination=False):
    # Import the module's default app with an isolated offline state directory.
    with patch.dict(os.environ, {"XUNQI_DATA_MODE": "offline", "XUNQI_RUNTIME_DIR": str(directory)}):
        from stay_agent.main import create_app
    with patch.dict(os.environ, {"XUNQI_DATA_MODE": "live"}):
        app = create_app(
            session_store=SessionStore(directory / "search.sqlite3"),
            conversation_service=ConversationService(ConversationStore(directory / "conversations.sqlite3")),
        )
    allowed_searches = [expected]
    if destination:
        allowed_searches.append({**expected, "poi_name": ANCHOR["name"], "limit": 10})
    available_post = {"/api/places/resolve", "/api/places/nearby/parallel", "/api/places/walk"} if destination else set()
    searched = 0
    detailed = False

    @app.middleware("http")
    async def scope(request, call_next):
        nonlocal searched, detailed
        path = request.url.path
        if path == "/api/search/parallel" and request.method == "POST":
            body = await request.json()
            if (searched < len(allowed_searches) and isinstance(body, dict)
                    and body.get("query") == allowed_searches[searched]):
                searched += 1
                return await call_next(request)
        elif searched and request.method == "POST" and path in available_post:
            available_post.remove(path)
            return await call_next(request)
        elif request.method == "POST" and re.fullmatch(r"/api/hotels/[0-9]+/quotes", path):
            if searched and not detailed:
                detailed = True
                return await call_next(request)
        elif request.method == "GET" and (not path.startswith("/api/") or path == "/api/health"):
            return await call_next(request)
        return JSONResponse({"error": {"code": "R120_SCOPE_DENIED", "message": "本轮操作不在验收范围。"}},
                            status_code=403)

    return app


def browser_run(directory, expected, budget, destination=False):
    denied = patch("http.client.HTTPConnection.request", side_effect=BudgetExceeded("ENDPOINT_DENIED"))
    with (run.guard_http(budget, hosts=HOSTS, loopback_tunnels=True), denied, socket.socket() as sock,
          patch.dict(os.environ, {"XUNQI_FLYAI_MODE": "cli" if destination else "mcp"}),
          patch("stay_agent.providers.data.flyai_client._run_cli", metered_cli(budget))):
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(build(directory, expected, destination),
                                              log_level="error", access_log=False))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]})
        worker.start()
        try:
            until = time.monotonic() + 8
            while not server.started and worker.is_alive() and time.monotonic() < until:
                time.sleep(0.05)
            if not server.started:
                return 2
            return subprocess.run(
                ["node", str(Path(__file__).with_name("r120_browser.cjs")), str(directory), str(port)],
                capture_output=True, timeout=300, check=False,
            ).returncode
        finally:
            server.should_exit = True
            worker.join()


def capture(directory, run_once, limits):
    directory.mkdir(parents=True, exist_ok=False)
    budget = Budget.create(directory / "budget.sqlite", limits)
    try:
        return run_once(budget)
    finally:
        for source in limits:
            budget.stop(source)
        run.write(directory / "budget-summary.json", budget.summary())
        with closing(budget.connect()) as db:
            rows = db.execute("SELECT source,status,count(*) FROM attempts GROUP BY source,status").fetchall()
        run.write(directory / "http-status-counts.json", rows)


def prove_flyai(directory, expected, budget):
    denied = patch("http.client.HTTPConnection.request", side_effect=BudgetExceeded("ENDPOINT_DENIED"))
    with (run.guard_http(budget, hosts={"flyai.open.fliggy.com": "flyai"}, loopback_tunnels=True),
          denied, patch.dict(os.environ, {"XUNQI_FLYAI_MODE": "mcp"})):
        context = SourceContext("flyai", 1, time.monotonic() + 30, time.monotonic, threading.Event())
        result = parallel_flyai_adapter(SearchInput.model_validate(expected), context)
    run.write(directory / "result.json", {
        "status": result.status, "reason": result.unavailable_reason,
        "candidates": len(result.candidates), "display_prices": sum(row.price_text is not None for row in result.candidates),
    })
    return 0


def metered_cli(budget):
    script_path = Path(__file__).with_name("r120_flyai_guard.cjs")

    def runner(command, env, timeout):
        guarded = {**env, "XUNQI_EVAL_PYTHON": sys.executable, "XUNQI_EVAL_LEDGER": str(Path(__file__).with_name("ledger.py")),
                   "XUNQI_EVAL_DB": str(budget.path)}
        guarded.pop("NODE_OPTIONS", None)
        guarded_command = [command[0], "--require", str(script_path), *command[1:]]
        process = subprocess.run(guarded_command, env=guarded, timeout=timeout, encoding="utf-8",
                                 errors="replace", capture_output=True, check=False)
        return process.returncode, process.stdout, process.stderr
    return runner


def prove_flyai_cli(directory, expected, budget):
    key = load_key()
    if not key:
        return 4
    client = FlyAICliClient(key, max_requests=1, timeout=35, runner=metered_cli(budget))
    provider = FlyAIDiscoveryProvider(search_callback(client), mode="cli")
    try:
        evidence = provider.search(SearchInput.model_validate(expected), city=expected["place"])
        result = {"status": evidence.status, "candidates": len(evidence.hints),
                  "display_prices": sum(row.price_text is not None for row in evidence.hints)}
    except TuniuFailure as error:
        result = {"status": "unavailable", "reason": error.code, "candidates": 0}
    run.write(directory / "result.json", result)
    return 0


def prove_rollinggo_destination(directory, expected, budget):
    query = SearchInput.model_validate({**expected, "poi_name": "海底捞火锅(北京路店)", "limit": 10})
    denied = patch("http.client.HTTPConnection.request", side_effect=BudgetExceeded("ENDPOINT_DENIED"))
    with run.guard_http(budget, hosts={"mcp.rollinggo.cn": "rollinggo"}), denied:
        result = QuoteService().search(query, SessionStore(directory / "search.sqlite3"))
    prices = [hotel.reference_price_fen for hotel in result.hotels
              if hotel.currency == "CNY" and hotel.reference_price_fen is not None]
    run.write(directory / "result.json", {
        "status": result.status, "reason": result.error.code if result.error else None,
        "candidates": len(result.hotels), "priced_count": len(prices),
        "lowest_reference_fen": min(prices, default=None),
    })
    return 0


def prove_rollinggo_room(directory, expected, budget):
    source_path = DIRECTORY.with_name(DIRECTORY.name + "-rollinggo-destination") / "search.sqlite3"
    db_uri = source_path.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(db_uri, uri=True)) as db:
        ids = db.execute("SELECT request_id FROM search_sessions LIMIT 2").fetchall()
    if len(ids) != 1:
        return 4
    request_id, = ids[0]
    store = SessionStore(source_path)
    context, hotels = store.list_hotels(request_id)
    if (len(hotels) != 1 or context != {
        **expected, "poi_name": "海底捞火锅(北京路店)", "limit": 10, "_supplier_source": "rollinggo",
    }):
        return 4
    hotel_id = hotels[0]["id"]
    query = SearchInput.model_validate(store.load(request_id, hotel_id))
    denied = patch("http.client.HTTPConnection.request", side_effect=BudgetExceeded("ENDPOINT_DENIED"))
    with run.guard_http(budget, hosts={"mcp.rollinggo.cn": "rollinggo"}), denied:
        result = QuoteService().quotes(request_id, hotel_id, query)
    run.write(directory / "result.json", {
        "status": result.status, "reason": result.error.code if result.error else None,
        "bound": result.request_id == request_id and result.hotel_id == hotel_id and result.source.id == "rollinggo",
        "quotes": len(result.quotes), "occupancy_status": result.occupancy_status,
        "bookable": bool(result.bookability and result.bookability.ready),
    })
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true")
    modes.add_argument("--flyai-proof", action="store_true")
    modes.add_argument("--flyai-cli-proof", action="store_true")
    modes.add_argument("--rollinggo-destination-proof", action="store_true")
    modes.add_argument("--rollinggo-room-proof", action="store_true")
    modes.add_argument("--destination-web", action="store_true")
    args = parser.parse_args()
    expected = SearchInput.model_validate(QUERY).model_dump(mode="json")
    if not any(vars(args).values()):
        print("PREFLIGHT_PASS")
        return 0
    if datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat() != DAY:
        return 3
    if args.flyai_proof:
        directory = DIRECTORY.with_name(DIRECTORY.name + "-flyai")
        return capture(directory, lambda budget: prove_flyai(directory, expected, budget), {"flyai": 1})
    if args.flyai_cli_proof:
        directory = DIRECTORY.with_name(DIRECTORY.name + "-flyai-cli-guarded")
        return capture(directory, lambda budget: prove_flyai_cli(directory, expected, budget), {"flyai": 1})
    if args.rollinggo_destination_proof:
        directory = DIRECTORY.with_name(DIRECTORY.name + "-rollinggo-destination")
        return capture(directory, lambda budget: prove_rollinggo_destination(directory, expected, budget),
                       {"rollinggo": 5})
    if args.rollinggo_room_proof:
        directory = DIRECTORY.with_name(DIRECTORY.name + "-rollinggo-room")
        return capture(directory, lambda budget: prove_rollinggo_room(directory, expected, budget),
                       {"rollinggo": 5})
    if args.destination_web:
        directory = DIRECTORY.with_name(DIRECTORY.name + "-destination-web")

        def run_destination(budget):
            run.write(directory / "manifest.json", {
                "day": DAY, "limits": DEST_LIMITS, "query": expected, "anchor": ANCHOR,
            })
            return browser_run(directory, expected, budget, destination=True)

        return capture(directory, run_destination, DEST_LIMITS)

    def run_browser(budget):
        run.write(DIRECTORY / "manifest.json", {"day": DAY, "limits": LIMITS, "query": expected})
        return browser_run(DIRECTORY, expected, budget)

    return capture(DIRECTORY, run_browser, LIMITS)


if __name__ == "__main__":
    sys.exit(main())
