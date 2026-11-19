"""R93 architecture/ASGI boundary invariants, entirely offline."""
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from scripts.check_architecture import check, import_violations
from stay_agent.http_boundary import LocalRequestGuard, error_response
from stay_agent.main import LocalRequestGuard as WiredLocalRequestGuard


def test_architecture_manifest_matches_current_code():
    assert check() == []
    assert WiredLocalRequestGuard is LocalRequestGuard


def test_pure_domain_must_not_depend_on_suppliers_or_http():
    assert import_violations("from .qualification import room_form\n") == []
    violations = import_violations(
        "import requests\nfrom .providers.data import rollinggo\nfrom .main import app\n"
    )
    assert violations == [".main", ".providers.data", "requests"]
    assert import_violations("from . import providers\n") == [".providers"]
    assert import_violations("import stay_agent.main\n") == ["stay_agent.main"]


def guard_client() -> TestClient:
    app = FastAPI()

    @app.post("/api/echo")
    async def echo(request: Request):
        return {"value": await request.json()}

    @app.get("/api/fail")
    def fail():
        return error_response("NO_ACCESS", "请求受限。", 403)

    app.add_middleware(LocalRequestGuard)
    return TestClient(app)


def test_asgi_guard_keeps_api_no_store_and_structured_errors():
    with guard_client() as client:
        result = client.post("/api/echo", json={"test": "ok"})
        assert result.status_code == 200
        assert result.json() == {"value": {"test": "ok"}}
        assert result.headers["cache-control"] == "no-store"
        error = client.get("/api/fail")
        assert error.status_code == 403
        assert error.json() == {"error": {"code": "NO_ACCESS", "message": "请求受限。"}}
        assert error.headers["cache-control"] == "no-store"


def test_asgi_guard_still_rejects_foreign_origins():
    with guard_client() as client:
        response = client.post(
            "/api/echo", json={"test": 1}, headers={"Origin": "https://example.com"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ORIGIN_DENIED"


def test_asgi_guard_still_rejects_large_request_bodies():
    with guard_client() as client:
        response = client.post("/api/echo", json={"large": "x" * 17000})
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
