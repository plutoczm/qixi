"""Loopback-only FastAPI application for the approved quote-browser scope."""

from contextlib import asynccontextmanager
from pathlib import Path
import logging
import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .contracts import (
    QuoteRequest,
    QuotesResponse,
    SearchInput,
    SearchResponse,
    SearchPageRequest,
    china_today,
    TravelPlaceRequest,
    TravelPlacesResponse,
    TravelRouteRequest,
    TravelRouteResponse,
    ConversationState,
    InterpretRequest,
    CancelInterpretRequest,
)
from .conversation import ConversationService
from .conversation_store import ConversationError
from .decision import build_quote_decision, build_route_assessment
from .discovery_service import DiscoveryError, DiscoveryService
from .generation import GenerationError
from .providers.llm.adapters import AdapterError
from stay_agent.providers.data.travel_clients import load_setting, load_key, ProbeError
from .service import QuoteService
from .http_boundary import LocalRequestGuard, error_response
from .tuniu_service import TuniuQuoteService
from .providers.data.tuniu import TuniuFailure
from .storage import SessionError, SessionStore
from .travel import TravelService, TravelError
from .poi_nearby import NearbyPlaceService
from .parallel_search import ParallelSearch, ParallelSearchRequest, ParallelSearchResponse

from .recall import RecallService, RecallError
from .recall_continuation import ContinuationError
from .branch_walk import BranchWalkService, BranchWalkError
from .contracts import (
    BranchWalkBatchRequest,
    BranchWalkBatchResponse,
    BranchWalkRouteRequest,
    BranchWalkPair,
    DiscoveryResponse,
)
from .contracts import RecallInput, RecallResponse, RecallContinueRequest
from .contracts import (
    NearbyResolveRequest, NearbyResolveResponse, NearbyExploreRequest,
    NearbyExploreResponse, NearbyWalkRequest, NearbyWalkResponse,
    NearbyIdentityStatusRequest, NearbyIdentityConfirmRequest,
    NearbyIdentityRevokeRequest, NearbyIdentityResponse,
    ParallelNearbyExploreRequest, ParallelNearbyExploreResponse,
)

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("xunqi")


def create_app(
    service=None,
    session_store=None,
    web_root=None,
    travel_service=None,
    conversation_service=None,
    recall_service=None,
    branch_walk_service=None,
    discovery_service=None,
    offline_demo=False,
    nearby_service=None,
    parallel_service=None,
):
    data_mode = os.environ.get("XUNQI_DATA_MODE", "live").strip()
    if data_mode not in ("offline", "live"):
        raise ValueError("INVALID_DATA_MODE")
    if service is None and data_mode == "offline":
        from .offline_app import build_offline_app
        import os as _os

        runtime_root = _os.environ.get("XUNQI_RUNTIME_DIR")
        return build_offline_app(web_root=web_root,
                                 runtime_root=Path(runtime_root) if runtime_root else None)
    offline_demo = offline_demo or (service is not None and data_mode == "offline")
    if service is not None:
        provider = service
    else:
        selected = os.environ.get("XUNQI_HOTEL_SOURCE", "rollinggo").strip()
        if selected not in ("tuniu", "rollinggo"):
            raise ValueError("INVALID_HOTEL_SOURCE")
        provider = TuniuQuoteService() if selected == "tuniu" else QuoteService()
    tuniu_source = isinstance(provider, TuniuQuoteService)
    parallel_enabled = not offline_demo and (service is None or parallel_service is not None)
    rollinggo = QuoteService() if tuniu_source else provider
    tuniu = provider if tuniu_source else TuniuQuoteService()
    parallel = (parallel_service or ParallelSearch(rollinggo, tuniu)) if parallel_enabled else None
    sessions = (
        session_store
        if session_store is not None
        else SessionStore(ROOT / "runtime/search.sqlite3")
    )
    dist = Path(web_root) if web_root is not None else ROOT / "frontend/dist"
    conversation = (
        conversation_service if conversation_service is not None else ConversationService()
    )

    recall = recall_service if recall_service is not None else RecallService(quote_service=provider)

    discovery = (
        discovery_service if discovery_service is not None else DiscoveryService()
    )

    branch_walk = (
        branch_walk_service
        if branch_walk_service is not None
        else BranchWalkService(recall, sessions)
    )

    @asynccontextmanager
    async def lifespan(app):
        conversation.recover()
        yield
        branch_walk.close()
        recall.close()
        conversation.close()
        if parallel is not None:
            parallel.close()

    app = FastAPI(
        title="寻栖房价查询", version="0.9.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    offline_checkpoint_routes = None
    if offline_demo:
        from .checkpoint import (
            CheckpointError, create_checkpoint, health_payload, inspect_checkpoint,
        )
        from fastapi.responses import JSONResponse as _JSONResponse
        import datetime as _datetime

        def checkpoint_status():
            return _checkpoint_state()

        def _checkpoint_state():
            label = _latest_checkpoint()
            if label is None:
                return health_payload(True)
            try:
                return health_payload(True, inspect_checkpoint(
                    ROOT, label, allow_commit_mismatch=True,
                    runtime_root=getattr(app.state, "runtime_root", None),
                ))
            except CheckpointError:
                return health_payload(True)

        def _latest_checkpoint():
            from pathlib import Path as _Path

            directory = _Path(getattr(app.state, "runtime_root", None) or ROOT / "runtime") / "checkpoints"
            if not directory.is_dir():
                return None
            try:
                candidates = sorted(
                    (path for path in directory.iterdir()
                     if path.is_dir() and (path / "checkpoint.json").is_file()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                return candidates[0].name if candidates else None
            except OSError:
                return None

        def save_checkpoint():
            label = "break-" + _datetime.datetime.now(
                _datetime.timezone.utc
            ).strftime("%Y%m%dT%H%M%S%fZ")
            try:
                path = create_checkpoint(
                    ROOT, label, runtime_root=getattr(app.state, "runtime_root", None),
                )
            except CheckpointError as error:
                return _JSONResponse(
                    {"error": {"code": str(error), "message": "断点保存失败，现有数据保持不变。"}},
                    status_code=503,
                )
            return {"status": "saved", "checkpoint": health_payload(False,
                    inspect_checkpoint(ROOT, path.name, allow_commit_mismatch=True,
                                       runtime_root=getattr(app.state, "runtime_root", None)))}

        def resume_checkpoint():
            label = _latest_checkpoint()
            if label is None:
                return {
                    "status": "unavailable",
                    "checkpoint_id": None,
                    "saved_at": None,
                    "disconnected_at": None,
                    "search": None,
                    "conversation": None,
                    "warnings": ["没有找到可恢复的断点记录。"],
                    "error": {"code": "CHECKPOINT_NOT_FOUND", "message": "请先保存断点。"},
                }
            from .offline_app import build_checkpoint_resume

            return build_checkpoint_resume(
                provider, conversation, ROOT, label,
                runtime_root=getattr(app.state, "runtime_root", None),
            )

        offline_checkpoint_routes = {
            "get": checkpoint_status, "post": save_checkpoint, "resume": resume_checkpoint,
        }

    app.state.conversation = conversation
    app.state.service = provider
    app.state.sessions = sessions
    travel = (
        travel_service if travel_service is not None
        else None if offline_demo else TravelService()
    )
    app.state.travel = travel
    nearby = nearby_service if nearby_service is not None else NearbyPlaceService(
        supplier_source="tuniu" if isinstance(provider,TuniuQuoteService) else "rollinggo",
    )
    if nearby.city_observations is None:
        nearby.city_observations = getattr(rollinggo, "city_observations", None)
    app.state.nearby = nearby
    app.add_middleware(LocalRequestGuard)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response("INVALID_INPUT", "请检查地点、日期和入住人数。", 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("request_failed error_type=%s", type(exc).__name__)
        return error_response("INTERNAL_ERROR", "查询暂时不可用，请稍后重试。", 500)

    @app.get("/api/health")
    def health():
        from .offline_dataset import DATASET_ID, DATASET_LABEL, DATASET_VERSION
        from .providers.data.rollinggo import ConfigurationError, load_api_key
        from .providers.data.tuniu_mcp import TuniuConfigurationError, auth_configuration

        def configured(check):
            try:
                check()
                return True
            except (ProbeError, ConfigurationError, TuniuConfigurationError, OSError):
                return False

        if offline_demo:
            channels = {}
        else:
            channels = {
                "quote": {
                    "provider": "tuniu" if tuniu_source else "rollinggo",
                    "configured": provider.credential_configured(),
                    "live_verified": False,
                },
                "rollinggo": {
                    "provider": "rollinggo",
                    "configured": configured(load_api_key),
                    "live_verified": False,
                },
                "tuniu": {
                    "provider": "tuniu",
                    "configured": configured(auth_configuration),
                    "live_verified": False,
                },
                "amap": {
                    "provider": "amap",
                    "configured": configured(lambda: load_key("amap")),
                    "live_verified": False,
                },
                "tencent": {
                    "provider": "tencent",
                    "configured": configured(lambda: load_key("tencent")),
                    "live_verified": False,
                },
                "didi": {
                    "provider": "didi",
                    "configured": configured(lambda: load_key("didi")),
                    "live_verified": False,
                },
                "deepseek": {
                    "provider": "deepseek",
                    "configured": configured(lambda: load_setting("DEEPSEEK_API_KEY")),
                    "live_verified": False,
                },
            }

        return {
            "status": "ok",
            "provider": "tuniu" if tuniu_source else "rollinggo",
            "credential_configured": provider.credential_configured(),
            "web_ready": (dist / "index.html").is_file(),
            "today_shanghai": china_today().isoformat(),
            "features": {"maps": True, "fare": True, "llm": True,
                         "multi_branch_recall": not tuniu_source,
                         **({"parallel_search": True} if parallel_enabled else {})},
            "data_mode": "offline" if offline_demo else "live",
            "dataset": None if not offline_demo else {
                "id": DATASET_ID,
                "label": DATASET_LABEL,
                "version": DATASET_VERSION,
                "synthetic": True,
            },
            "channels": channels,
        }

    @app.exception_handler(RecallError)
    async def recall_error(request, exc):
        if exc.code in {"RECALL_ASSOCIATION_NOT_FOUND", "RECALL_ASSOCIATION_EXPIRED"}:
            return error_response(
                exc.code,
                "召回关联已过期或服务已重启，请重新召回后核对。",
                410 if exc.code.endswith("EXPIRED") else 404,
            )
        return error_response(
            exc.code,
            "多门店查询繁忙、已结束或编号重复，请重新发起。",
            409 if exc.code == "RECALL_OPERATION_CONFLICT" else 429,
        )

    @app.exception_handler(BranchWalkError)
    async def walk_error(request, exc):
        messages = {
            "WALK_CONTEXT_EXPIRED": "点位已过期，请重新批量核对。",
            "WALK_CONTEXT_NOT_FOUND": "未找到核对点位，请重新批量核对。",
            "WALK_CONTEXT_MISMATCH": "点位不属于本次召回，不能复用。",
            "WALK_IDENTITY_REQUIRED": "请分别核对并确认住宿与原召回门店。",
            "WALK_RESTAURANT_ROLE_REQUIRED": "当前门店尚未通过堂食角色门禁，不能查询堂食返程路线。",
            "WALK_OPTION_MISMATCH": "所选地点不属于这一组核对，不能交换角色。",
            "WALK_BUSY": "核对繁忙或点位名额不足，请稍后再试。",
            "CANCELLED": "本次核对已停止，请重新发起。",
            "WALK_NO_ROUTE": "本次未取得实际步行路线，不能认定达标。",
        }
        status = (
            410
            if exc.code == "WALK_CONTEXT_EXPIRED"
            else 404
            if exc.code == "WALK_CONTEXT_NOT_FOUND"
            else 429
            if exc.code == "WALK_BUSY"
            else 409
            if exc.code
            in {
                "WALK_CONTEXT_MISMATCH",
                "WALK_IDENTITY_REQUIRED",
                "WALK_RESTAURANT_ROLE_REQUIRED",
                "CANCELLED",
                "WALK_OPERATION_CONFLICT",
            }
            else 422
        )
        return error_response(
            exc.code, messages.get(exc.code, "请重新发起位置与步行核对。"), status
        )

    @app.post("/api/branch-walk/batch", response_model=BranchWalkBatchResponse)
    def branch_walk_batch(request: BranchWalkBatchRequest):
        if offline_demo:
            return error_response("OFFLINE_DEMO_UNSUPPORTED",
                                  "离线演示版不包含多门店路线工作流；未调用外部来源。", 503)
        if tuniu_source:
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE",
                                  "途牛多门店路线核验尚未适配，未调用旧来源。", 503)
        return branch_walk.batch(request)

    @app.post("/api/branch-walk/route", response_model=BranchWalkPair)
    def branch_walk_route(request: BranchWalkRouteRequest):
        if offline_demo:
            return error_response("OFFLINE_DEMO_UNSUPPORTED",
                                  "离线演示版不包含多门店路线工作流；未调用外部来源。", 503)
        if tuniu_source:
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE",
                                  "途牛多门店路线核验尚未适配，未调用旧来源。", 503)
        return branch_walk.route(request)

    @app.post("/api/branch-walk/{operation_id}/cancel")
    def branch_walk_cancel(operation_id: str):
        import re

        if not re.fullmatch(r"[a-f0-9-]{32,36}", operation_id):
            return error_response("INVALID_INPUT", "操作编号无效。", 422)
        return branch_walk.cancel(operation_id)

    @app.exception_handler(ContinuationError)
    async def continuation_error(request, exc):
        messages = {
            "CONTINUATION_NOT_FOUND": "续查队列不存在或服务已重启，请重新查询。",
            "CONTINUATION_EXPIRED": "续查队列或原搜索已过期，请重新查询。",
            "CONTINUATION_STALE": "续查状态已变化；不重放旧请求，请重新查询。",
            "CONTINUATION_BUSY": "该队列正在续查或查询繁忙，请等待后再操作。",
            "CANCELLED": "已停止续查，已发出的名称不会自动重试。",
        }
        status = (
            404
            if exc.code == "CONTINUATION_NOT_FOUND"
            else 410
            if exc.code == "CONTINUATION_EXPIRED"
            else 429
            if exc.code == "CONTINUATION_BUSY"
            else 409
        )
        return error_response(
            exc.code, messages.get(exc.code, "续查未完成，请核对后再操作。"), status
        )

    @app.post("/api/recall/continue", response_model=RecallResponse)
    def recall_continue(request: RecallContinueRequest):
        if offline_demo:
            return error_response("OFFLINE_DEMO_UNSUPPORTED",
                                  "离线演示版不包含多门店续查；未调用外部来源。", 503)
        if tuniu_source:
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE",
                                  "途牛多门店续查尚未适配，未调用旧道旅。", 503)
        return recall.continuation.run(request, sessions)

    @app.post("/api/recall", response_model=RecallResponse)
    def recall_search(request: RecallInput):
        if offline_demo:
            return error_response("OFFLINE_DEMO_UNSUPPORTED",
                                  "离线演示版只包含固定候选，请使用普通查询；未调用外部来源。", 503)
        if tuniu_source:
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE",
                                  "途牛多门店召回尚未适配，请先使用单城市酒店搜索。", 503)
        return recall.search(request, sessions)

    @app.post("/api/recall/{operation_id}/cancel")
    def recall_cancel(operation_id: str):
        import re

        if not re.fullmatch(r"[a-f0-9-]{32,36}", operation_id):
            return error_response("INVALID_INPUT", "操作编号无效。", 422)
        return recall.cancel(operation_id)

    @app.post("/api/places/resolve", response_model=NearbyResolveResponse)
    def resolve_nearby_place(request: NearbyResolveRequest):
        if offline_demo:
            return error_response(
                "OFFLINE_DEMO_UNSUPPORTED",
                "离线夹具不包含真实地点解析；没有调用地图来源。", 503,
            )
        return nearby.resolve(request)

    @app.post("/api/places/nearby", response_model=NearbyExploreResponse)
    def explore_nearby(request: NearbyExploreRequest):
        if offline_demo:
            return error_response(
                "OFFLINE_DEMO_UNSUPPORTED",
                "离线夹具不包含周边真实地点；没有调用地图来源。", 503,
            )
        return nearby.explore(request, sessions)

    @app.post("/api/places/nearby/parallel", response_model=ParallelNearbyExploreResponse)
    def explore_parallel_nearby(request: ParallelNearbyExploreRequest):
        if parallel is None:
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE", "当前模式未启用三家并行查询。", 503)
        ids = parallel.nearby_requests(request.operation_id)
        if request.request_id is not None and request.request_id not in ids:
            return error_response("SEARCH_NOT_FOUND", "住宿会话不属于本次并行查询。", 404)
        return nearby.explore(request, sessions, recommendation_requests=ids)

    @app.post("/api/places/walk", response_model=NearbyWalkResponse)
    def walk_nearby(request: NearbyWalkRequest):
        if offline_demo:
            return error_response(
                "OFFLINE_DEMO_UNSUPPORTED",
                "离线夹具不包含真实步行路线；没有调用地图来源。", 503,
            )
        return nearby.walk(request)

    def local_identity(request, action):
        if offline_demo:
            return error_response("OFFLINE_DEMO_UNSUPPORTED", "离线夹具不提供同店身份确认。", 503)
        result = nearby.identity(request, sessions, action)
        if result["status"] == "unavailable":
            from fastapi.responses import JSONResponse
            payload = NearbyIdentityResponse.model_validate(result).model_dump(mode="json")
            code = result["error"].code
            status = (410 if code in {"CONTEXT_EXPIRED", "SOURCE_SESSION_MISSING"} else
                      404 if code == "CONTEXT_NOT_FOUND" else 503 if code == "BUSY" else 409)
            return JSONResponse(payload, status_code=status)
        return result

    @app.post("/api/places/identity/status", response_model=NearbyIdentityResponse)
    def nearby_identity_status(request: NearbyIdentityStatusRequest):
        return local_identity(request, "status")

    @app.post("/api/places/identity/confirm", response_model=NearbyIdentityResponse)
    def nearby_identity_confirm(request: NearbyIdentityConfirmRequest):
        return local_identity(request, "confirm")

    @app.post("/api/places/identity/revoke", response_model=NearbyIdentityResponse)
    def nearby_identity_revoke(request: NearbyIdentityRevokeRequest):
        return local_identity(request, "revoke")

    @app.post("/api/search", response_model=SearchResponse)
    def search(query: SearchInput):
        return provider.search(query, sessions)

    @app.post("/api/search/parallel", response_model=ParallelSearchResponse)
    def search_parallel(request: ParallelSearchRequest):
        if parallel is None:
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE", "当前模式未启用三家并行查询。", 503)
        try:
            return parallel.search(request, sessions)
        except SessionError as error:
            return error_response(error.code, "并行查询已存在或当前繁忙。", 409)

    @app.post("/api/search/parallel/{operation_id}/cancel")
    def cancel_parallel_search(operation_id: str):
        if parallel is None:
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE", "当前模式未启用三家并行查询。", 503)
        if not 32 <= len(operation_id) <= 36 or any(c not in "0123456789abcdef-" for c in operation_id):
            return error_response("INVALID_OPERATION_ID", "查询编号无效。", 422)
        try:
            parallel.cancel(operation_id)
        except SessionError as error:
            return error_response(error.code, "当前查询繁忙。", 409)
        return {"accepted": True}

    @app.post("/api/discovery", response_model=DiscoveryResponse)
    def discovery_search(query: SearchInput):
        """Candidate discovery only: one bounded call, no quote, no merge."""
        if offline_demo:
            return discovery.search(query)
        try:
            return discovery.search(query)
        except DiscoveryError as error:
            return error_response(
                error.code,
                "飞猪候选只支持按城市查询；请把地点类型改为城市。",
                422,
            )

    @app.post("/api/search/next", response_model=SearchResponse)
    def search_next(request: SearchPageRequest):
        if offline_demo:
            return error_response("OFFLINE_DEMO_UNSUPPORTED",
                                  "离线演示数据集没有分页；未调用外部来源。", 503)
        if not (tuniu_source or parallel_enabled):
            return error_response("SOURCE_WORKFLOW_UNAVAILABLE",
                                  "当前来源不支持此途牛专用分页。", 503)
        try:
            sessions.ensure_live(request.request_id)
            return tuniu.next_page(request.request_id, request.expected_page, sessions)
        except SessionError:
            return error_response("SEARCH_EXPIRED", tuniu.error("SEARCH_EXPIRED").message, 409)
        except TuniuFailure as failure:
            status = 429 if failure.code in ("RATE_LIMITED", "REQUEST_LIMIT") else 409
            return error_response(failure.code, tuniu.error(failure.code).message, status)

    @app.post("/api/hotels/{hotel_id}/quotes", response_model=QuotesResponse)
    def quotes(hotel_id: int, query: QuoteRequest):
        if not 0 < hotel_id < 2**31:
            return error_response("INVALID_HOTEL_ID", "住宿编号无效，请重新搜索。", 422)
        try:
            context = sessions.load(query.request_id, hotel_id, include_source=True)
        except SessionError as error:
            messages = {
                "SEARCH_NOT_FOUND": "未找到这次搜索，请重新查询。",
                "SEARCH_EXPIRED": "这次搜索已过期，请重新查询。",
                "HOTEL_NOT_IN_SEARCH": "该住宿不属于这次搜索，请重新查询。",
            }
            return error_response(
                error.code,
                messages.get(error.code, "请重新搜索。"),
                410 if error.code == "SEARCH_EXPIRED" else 404,
            )
        try:
            source = context.pop("_supplier_source", None)
            original_query = SearchInput.model_validate(context)
        except ValidationError:
            return error_response("SEARCH_EXPIRED", "搜索日期已失效，请重新查询。", 410)
        quote_provider = parallel.services.get(source, provider) if parallel is not None else provider
        result = quote_provider.quotes(query.request_id, hotel_id, original_query)
        result.decision = build_quote_decision(result)
        return result

    @app.exception_handler(TravelError)
    async def travel_error(request, exc):
        messages = {
            "TRAVEL_CONTEXT_NOT_FOUND": "未找到地点查询，请重新检索地点。",
            "TRAVEL_CONTEXT_EXPIRED": "地点选择已过期，请重新检索地点。",
            "TRAVEL_CONTEXT_MISMATCH": "地点与本次住宿查询不一致，请重新检索。",
            "PLACE_NOT_IN_CONTEXT": "所选地点不属于本次查询，请重新检索。",
            "HOTEL_CONFIRMATION_REQUIRED": "请先核对住宿名称和地址，并确认对应同一家住宿。",
        }
        status = (
            410
            if exc.code == "TRAVEL_CONTEXT_EXPIRED"
            else 404
            if exc.code == "TRAVEL_CONTEXT_NOT_FOUND"
            else 409
            if exc.code == "TRAVEL_CONTEXT_MISMATCH"
            else 422
        )
        return error_response(exc.code, messages.get(exc.code, "请重新检索地点。"), status)

    @app.exception_handler(SessionError)
    async def session_error(request, exc):
        return error_response(
            exc.code,
            "住宿查询已失效或缺少地点身份，请重新搜索住宿。",
            404 if exc.code in ("SEARCH_NOT_FOUND", "HOTEL_NOT_IN_SEARCH") else 410,
        )

    def travel_member(hotel_id, request_id):
        if not 0 < hotel_id < 2**31:
            raise SessionError("INVALID_HOTEL_ID")
        if request_id.startswith("flyai:"):
            if parallel is None:
                raise SessionError("SEARCH_EXPIRED")
            return parallel.discovery_hotel(request_id.removeprefix("flyai:"), hotel_id)
        saved = sessions.load(request_id, hotel_id)
        try:
            return SearchInput.model_validate(saved), sessions.hotel(request_id, hotel_id)
        except ValidationError:
            raise SessionError("SEARCH_EXPIRED") from None

    @app.post("/api/hotels/{hotel_id}/travel/places", response_model=TravelPlacesResponse)
    def travel_places(hotel_id: int, request: TravelPlaceRequest):
        query, hotel = travel_member(hotel_id, request.request_id)
        return travel.places(hotel_id, hotel, query, request)

    @app.post("/api/hotels/{hotel_id}/travel/route", response_model=TravelRouteResponse)
    def travel_route(hotel_id: int, request: TravelRouteRequest):
        travel_member(hotel_id, request.request_id)
        result = travel.route(hotel_id, request)
        result.return_trip = build_route_assessment(result)
        return result

    def conversation_failure(exc):
        code = exc.code
        messages = {
            "PROVIDER_DISABLED": "GPT已暂缓，当前只使用DeepSeek。",
            "REVISION_CONFLICT": "条件版本已更新，请核对最新草稿后重发。",
            "STALE_OPERATION": "该输入已被更新的条件替代，请查看最新草稿。",
            "OPERATION_CONFLICT": "操作编号与本次请求不一致，请重新发送。",
            "MODEL_BUSY": "当前理解任务较多，请稍后重试。",
            "SENSITIVE_INPUT": "请删除密钥、令牌或完整手机号后重发；住宿查询不需要这些信息。",
            "CONVERSATION_LIMIT": "本次会话达到上限，请重新开始。",
            "CONVERSATION_OPERATION_LIMIT": "本次会话达到上限，请重新开始。",
            "PENDING_INPUT_LIMIT": "未完成的修改过多，请先停止或重新开始。",
            "CONVERSATION_NOT_FOUND": "未找到会话，请重新开始。",
            "CONVERSATION_EXPIRED": "会话已过期，请重新开始。",
            "STATE_UNAVAILABLE": "本机状态库暂时无法写入，本次操作未完成；请重试。",
            "STATE_NOT_COMMITTED": "条件已变化，本次变更未写入；请刷新后重试。",
        }
        status = (
            409
            if code in (
                "REVISION_CONFLICT",
                "STALE_OPERATION",
                "OPERATION_CONFLICT",
                "STATE_NOT_COMMITTED",
            )
            else 404
            if code == "CONVERSATION_NOT_FOUND"
            else 410
            if code == "CONVERSATION_EXPIRED"
            else 429
            if code == "MODEL_BUSY"
            else 503
            if code == "STATE_UNAVAILABLE"
            else 422
        )
        return error_response(
            code, messages.get(code, "理解暂时不可用，请重新开始或使用表单。"), status
        )

    @app.exception_handler(ConversationError)
    async def conversation_error(request, exc):
        return conversation_failure(exc)

    @app.exception_handler(GenerationError)
    async def generation_error(request, exc):
        return conversation_failure(exc)

    @app.exception_handler(AdapterError)
    async def adapter_error(request, exc):
        return conversation_failure(exc)

    @app.get("/api/models")
    def models():
        if offline_demo:
            return {
                "default_provider": "local",
                "providers": [
                    {"id": "deepseek", "name": "本地规则解析（离线演示）",
                     "enabled": True, "configured": True},
                    {"id": "pi_chatgpt", "name": "GPT（暂缓）", "enabled": False,
                     "configured": False},
                ],
            }
        try:
            configured = bool(load_setting("DEEPSEEK_API_KEY", required=False))
        except ProbeError:
            configured = False
        return {
            "default_provider": "deepseek",
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek V4.1 Flash",
                    "enabled": True,
                    "configured": configured,
                },
                {"id": "pi_chatgpt", "name": "GPT（暂缓）", "enabled": False, "configured": False},
            ],
        }

    @app.post("/api/conversations", response_model=ConversationState)
    def create_conversation():
        return conversation.create()

    @app.get("/api/conversations/{conversation_id}", response_model=ConversationState)
    def get_conversation(conversation_id: str):
        return conversation.view(conversation_id)

    @app.post("/api/conversations/{conversation_id}/operations", response_model=ConversationState)
    def interpret(conversation_id: str, request: InterpretRequest):
        return conversation.start(conversation_id, request)

    @app.post("/api/conversations/{conversation_id}/cancel", response_model=ConversationState)
    def cancel_interpret(conversation_id: str, request: CancelInterpretRequest):
        return conversation.cancel(conversation_id, request)

    if offline_checkpoint_routes:
        app.add_api_route("/api/checkpoint", offline_checkpoint_routes["get"], methods=["GET"])
        app.add_api_route("/api/checkpoint", offline_checkpoint_routes["post"], methods=["POST"])
        app.add_api_route(
            "/api/checkpoint/resume", offline_checkpoint_routes["resume"], methods=["GET"]
        )

    # Reserved API paths never fall through to an HTML page.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def unknown_api(path: str):
        return error_response("NOT_FOUND", "接口不存在。", 404)

    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
    return app


app = create_app()
