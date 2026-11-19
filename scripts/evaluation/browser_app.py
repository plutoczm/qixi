"""Local-only evaluation app. All non-conversation business routes are refused."""

# ruff: noqa: E402
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from fastapi.responses import JSONResponse
import uvicorn
from scripts.evaluation.run import transport
from stay_agent.conversation import ConversationService
from stay_agent.conversation_store import ConversationStore
from stay_agent.main import create_app
from stay_agent.providers.llm.adapters import ModelRouter
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore


if __name__ == "__main__":
    directory = Path(sys.argv[1]).resolve()
    if (
        not directory.is_relative_to(ROOT / "runtime")
        or not (directory / "budget.sqlite").is_file()
    ):
        raise ValueError("AUTHORIZED_RUN_REQUIRED")
    service = ConversationService(
        ConversationStore(directory / "browser-conversations.sqlite"),
        ModelRouter(transport(directory)),
    )
    app = create_app(
        service=QuoteService(client_factory=lambda: None),
        session_store=SessionStore(directory / "browser-search.sqlite"),
        conversation_service=service,
    )

    @app.middleware("http")
    async def only_authorized_routes(request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not (
            path in {"/api/health", "/api/models", "/api/conversations"}
            or path.startswith("/api/conversations/")
        ):
            return JSONResponse(
                {
                    "error": {
                        "code": "EVALUATION_ROUTE_DENIED",
                        "message": "评测隔离：此业务请求未开放。",
                    }
                },
                status_code=403,
            )
        return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=8016, log_level="warning", access_log=False)
