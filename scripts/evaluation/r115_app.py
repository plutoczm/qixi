"""One authorized, isolated R115 live UI run; never runs on import."""
# ruff: noqa: E402
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from fastapi.responses import JSONResponse
import uvicorn
from scripts.evaluation.ledger import Budget, BudgetExceeded
from scripts.evaluation.run import guard_http, write
from stay_agent.conversation import ConversationService
from stay_agent.conversation_store import ConversationStore
from stay_agent.main import create_app
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore

LIMITS = {"rollinggo": 12, "amap": 6}
RECOVERY_LIMITS = {"rollinggo": 8, "amap": 5}
ANCHOR = "海底捞火锅(恒宝广场店)"
YIJIA_LIMITS = {"rollinggo": 8, "amap": 3}
BEIJING_ROAD = "海底捞火锅(北京路店)"
YIJIA_TARGET = "怡佳公寓(北京路步行街公园前地铁站店)"
QUERY = {"place": "广州", "place_type": "城市", "check_in": "2026-11-23", "check_out": "2026-11-24",
         "adults": 2, "rooms": 1, "children_ages": [], "budget_fen": 30000, "country_region": "中国大陆"}


@contextmanager
def no_plain_http():
    original = http.client.HTTPConnection.request
    def denied(*args, **kwargs):
        raise BudgetExceeded("ENDPOINT_DENIED")
    http.client.HTTPConnection.request = denied
    try:
        yield
    finally:
        http.client.HTTPConnection.request = original


def build(directory, recovery=False, anchor=ANCHOR):
    service = QuoteService()
    conversation = ConversationService(ConversationStore(directory / "conversations.sqlite3"))
    app = create_app(service=service, session_store=SessionStore(directory / "search.sqlite3"),
                     conversation_service=conversation)
    seen_searches = set()

    @app.middleware("http")
    async def authorized_routes(request, call_next):
        path = request.url.path
        allowed = path in {"/api/health", "/api/search", "/api/places/resolve", "/api/places/nearby",
                           "/api/places/walk", "/api/places/identity/status", "/api/places/identity/confirm",
                           "/api/places/identity/revoke"}
        import re
        allowed = allowed or bool(re.fullmatch(r"/api/hotels/[0-9]+/quotes", path))
        if path.startswith("/api/") and not allowed:
            return JSONResponse({"error": {"code": "EVALUATION_ROUTE_DENIED", "message": "本次只验收目的地住宿与同店链路。"}}, status_code=403)
        if path == "/api/search":
            data = await request.json()
            kind = "destination" if data.get("poi_name") else "city"
            valid = all(data.get(key) == value for key, value in QUERY.items())
            if kind == "destination":
                valid = valid and data.get("poi_name") == anchor
            if not valid or kind in seen_searches or (recovery and kind == "city"):
                return JSONResponse({"error": {"code": "EVALUATION_QUERY_DENIED", "message": "本次条件不符或已搜索，不自动重复。"}}, status_code=409)
            seen_searches.add(kind)
        return await call_next(request)
    return app


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    directory = Path(sys.argv[2]).resolve()
    if not directory.is_relative_to(ROOT / "runtime"):
        raise ValueError("ISOLATED_RUNTIME_REQUIRED")
    if sys.argv[1] in {"init", "init-recovery", "init-yijia"}:
        yijia = sys.argv[1] == "init-yijia"
        recovery = sys.argv[1] != "init"
        limits = YIJIA_LIMITS if yijia else RECOVERY_LIMITS if recovery else LIMITS
        directory.mkdir(parents=True, exist_ok=False)
        Budget.create(directory / "budget.sqlite", limits)
        write(directory / "manifest.json", {"authorized_all_existing_quota": True, "limits": limits, "recovery": recovery,
              "query": QUERY, "keyword": BEIJING_ROAD if yijia else "恒宝广场海底捞",
              "sample": "yijia" if yijia else "hengbao",
              "hotel_target": YIJIA_TARGET if yijia else None, "baseline": "088cbd6" if yijia else "68c278c"})
        print("INITIALIZED_LIVE_SAMPLE")
        return
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    recovery = manifest.get("recovery", False)
    yijia = manifest.get("sample") == "yijia"
    limits = YIJIA_LIMITS if yijia else RECOVERY_LIMITS if recovery else LIMITS
    if manifest.get("limits") != limits or not manifest.get("authorized_all_existing_quota"):
        raise ValueError("MANIFEST_MISMATCH")
    (directory / "server.started").touch(exist_ok=False)
    budget = Budget(directory / "budget.sqlite")
    try:
        # HTTPS patch must be entered first: HTTPS keeps its metered method while
        # plain HTTP is subsequently disabled. No local API requests use http.client here.
        with guard_http(budget), no_plain_http():
            uvicorn.run(build(directory, recovery=recovery, anchor=BEIJING_ROAD if yijia else ANCHOR), host="127.0.0.1", port=8018, log_level="warning", access_log=False)
    finally:
        for source in limits:
            budget.stop(source)
        write(directory / "budget-summary.json", budget.summary())


if __name__ == "__main__":
    main()
