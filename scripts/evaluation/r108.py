"""R108 explicitly approved, single-run live regression; import never sends HTTP."""
# ruff: noqa: E402
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from scripts.evaluation.ledger import Budget
from scripts.evaluation.run import guard_http, transport, turn, write
from scripts.evaluation.scoring import score_turn
from stay_agent.conversation import ConversationService
from stay_agent.conversation_store import ConversationStore
from stay_agent.providers.llm.adapters import ModelRouter
from stay_agent.contracts import SearchInput, NearbyResolveRequest, NearbyExploreRequest, NearbyWalkRequest
from stay_agent.poi_nearby import NearbyPlaceService
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore
from stay_agent.travel_rules import normalized, city_name

LIMITS = {"deepseek": 12, "rollinggo": 8, "amap": 3}
CASES = [
 {"id": "occupancy", "turns": [
  ("北京2026-11-23住1晚，2间房，一共3个大人，没有儿童，每房每晚500元以内。",
   {"rooms": 2,"adults": None,"query_ready": False}),
  ("改成每房2成人，没有儿童。", {"rooms": 2,"adults": 2,"children_count": 0,"query_ready": True})]},
 {"id": "colloquial", "turns": [
  ("我想去广州，2026-11-23住一宿，就一间，两位大人，没小孩儿，一晚上别超过三百块。",
   {"place": "广州","nights": 1,"rooms": 1,"adults": 2,"children_count": 0,"budget_fen": 30000,"query_ready": True})]},
 {"id": "outside", "turns": [
  ("东京新宿2026-11-23住1晚，1间房2成人，没有小孩，每晚500元以内。",
   {"query_ready": False})]},
 {"id": "child_age", "turns": [
  ("广州2026-11-23住1晚，2间房，每房2成人，每房1名儿童，先不确定年龄，每房每晚300元。",
   {"rooms": 2,"adults": 2,"children_count": 1,"query_ready": False}),
  ("6岁", {"rooms": 2,"adults": 2,"children_count": 1,"children_ages": [6],"query_ready": True})]},
]


def error_code(error):
    return error.code if error else None


def llm(directory, budget):
    marker = directory / "llm.started"
    marker.touch(exist_ok=False)
    service = ConversationService(ConversationStore(directory / "conversations.sqlite"), ModelRouter(transport(directory)))
    rows = []
    try:
        for case in json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["cases"]:
            cid = service.create().conversation_id
            revision, prior = 0, None
            for seq, (text, expected) in enumerate(case["turns"], 1):
                try:
                    row = turn(service, cid, text, revision, seq)
                except Exception as error:
                    row = {"status": "driver_error", "error": getattr(error,"code",type(error).__name__), "draft": {}, "query_ready": False}
                row.update(case_id=case["id"], sequence=seq, input=text)
                row["score"] = score_turn(row, expected, prior)
                if case["id"] == "outside":
                    scope_ok = not row["query_ready"] and "中国大陆" in row.get("question", "")
                    row["scope_hint_passed"] = scope_ok
                rows.append(row)
                write(directory / "conversations.json", rows)
                write(directory / "budget-summary.json", budget.summary())
                print("TURN", case["id"], seq, row["status"], row["score"]["passed"], row["score"]["failed"], flush=True)
                if row["status"] != "succeeded" or row.get("error"):
                    budget.stop("deepseek")
                    return
                revision, prior = row["revision"], row
    finally:
        service.close()
        budget.stop("deepseek")
        write(directory / "budget-summary.json", budget.summary())


def destination(directory, budget):
    (directory / "destination.started").touch(exist_ok=False)
    nearby = NearbyPlaceService(supplier_source="rollinggo")
    store = SessionStore(directory / "search.sqlite")
    summary = {"status": "started"}
    try:
        with guard_http(budget):
            result = nearby.resolve(NearbyResolveRequest(provider="amap", city="广州",keyword="海底捞火锅(荔胜广场店)",country_region="中国大陆"))
            summary.update(resolve_status=result["status"],resolve_error=error_code(result.get("error")),candidate_count=len(result["options"]))
            exact = [p for p in result["options"] if normalized(p["name"])==normalized("海底捞火锅(荔胜广场店)") and city_name(p["city"])=="广州"]
            if result.get("error") or len(exact)!=1:
                summary["status"] = "stopped_no_unique_exact_anchor"
                return
            point = exact[0]
            public = {key:point[key] for key in ("name","city","address")}
            summary["anchor"] = public
            summary["status"] = "awaiting_user_anchor_confirmation"
            write(directory / "destination.json", summary)
            write(directory / "budget-summary.json", budget.summary())
            print("CONFIRM_ANCHOR", json.dumps(public,ensure_ascii=False),flush=True)
            ctx = nearby.contexts[result["context_id"]]
            while nearby.clock()<ctx.expires:
                reply = directory / "anchor-confirmation.json"
                if reply.exists():
                    decision = json.loads(reply.read_text(encoding="utf-8"))
                    if decision != {"confirm": True}:
                        summary["status"] = "stopped_anchor_rejected"
                        return
                    break
                time.sleep(0.25)
            else:
                summary["status"] = "stopped_anchor_confirmation_expired"
                return
            summary["anchor_confirmed_by_user"] = True
            query = SearchInput(place="广州",place_type="城市",poi_name=point["name"],check_in="2026-11-23",check_out="2026-11-24",adults=2,rooms=1,children_ages=[],budget_fen=30000,limit=10,country_region="中国大陆")
            service = QuoteService()
            response = service.search(query, store)
            summary.update(search_status=response.status,search_error=error_code(response.error),hotel_count=len(response.hotels))
            # Safe allowlisted names/addresses for coverage diagnosis; never URLs/coordinates.
            summary["supplier_candidates"] = [{"name":h.name,"address":h.address,"reference_price_fen":h.reference_price_fen,"currency":h.currency} for h in response.hotels]
            if response.error or response.status=="unavailable":
                summary["status"] = "stopped_supplier_search_failed"
                return
            if nearby.clock()>=ctx.expires:
                summary["status"] = "stopped_context_expired_after_search"
                return
            mapped = nearby.explore(NearbyExploreRequest(context_id=result["context_id"],anchor_id=point["id"],request_id=response.request_id),store)
            summary.update(nearby_status=mapped["status"],nearby_error=error_code(mapped.get("error")),nearby_count=len(mapped["hotels"]))
            summary["map_candidates"] = [{k:h[k] for k in ("name","city","address","source_match","straight_line_meters")} for h in mapped["hotels"]]
            if mapped.get("error"):
                summary["status"] = "stopped_nearby_failed"
                return
            prices = {h.id:h for h in response.hotels}
            linked = [h for h in mapped["hotels"] if h["source_match"]=="matched" and h["source_hotel_id"] in prices]
            summary["matched_count"] = len(linked)
            priced = [h for h in linked if prices[h["source_hotel_id"]].currency=="CNY" and prices[h["source_hotel_id"]].reference_price_fen is not None]
            if not priced:
                summary["status"] = "stopped_no_priced_identity_match"
                return
            source_order = {h.id:index for index,h in enumerate(response.hotels)}
            chosen = min(priced,key=lambda h:(prices[h["source_hotel_id"]].reference_price_fen,source_order[h["source_hotel_id"]]))
            walk = nearby.walk(NearbyWalkRequest(context_id=result["context_id"],hotel_option_id=chosen["id"]))
            summary.update(walk_status=walk["status"],walk_error=error_code(walk.get("error")),walk_metres=walk["distance_meters"])
            if walk.get("error"):
                summary["status"] = "stopped_walk_failed"
                return
            detail = service.quotes(response.request_id,chosen["source_hotel_id"],query)
            summary.update(detail_status=detail.status,detail_error=error_code(detail.error),quote_count=len(detail.quotes),occupancy_status=detail.occupancy_status,booking_status=detail.booking_status,bookable=bool(detail.bookability and detail.bookability.ready))
            summary["status"] = "completed_reference_chain" if not detail.error else "stopped_detail_failed"
    except Exception as error:
        summary.update(status="stopped_driver_error",error=getattr(error,"code",type(error).__name__))
    finally:
        budget.stop("amap")
        budget.stop("rollinggo")
        write(directory / "destination.json", summary)
        write(directory / "budget-summary.json", budget.summary())
        print("DESTINATION",summary["status"],flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("phase",choices=["init","llm","destination"])
    parser.add_argument("--run-dir",required=True,type=Path)
    args=parser.parse_args()
    directory=args.run_dir.resolve()
    if not directory.is_relative_to(ROOT / "runtime"):
        raise ValueError("ISOLATED_RUNTIME_REQUIRED")
    if args.phase=="init":
        directory.mkdir(parents=True,exist_ok=False)
        Budget.create(directory / "budget.sqlite",LIMITS)
        write(directory / "manifest.json",{"created_at":datetime.now(timezone.utc).isoformat(),"limits":LIMITS,"approved":True,"cases":CASES,"product_baseline":"5b1c33a"})
        print("INITIALIZED",directory)
        return
    manifest=json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("limits")!=LIMITS or not manifest.get("approved"):
        raise ValueError("APPROVED_MANIFEST_MISMATCH")
    budget=Budget(directory / "budget.sqlite")
    {"llm":llm,"destination":destination}[args.phase](directory,budget)

if __name__=="__main__":
    main()
