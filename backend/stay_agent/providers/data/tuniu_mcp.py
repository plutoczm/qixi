"""Pinned, read-only Tuniu MCP transport. No implicit OAuth login or order API.

Two official modes are supported, selected explicitly:
- oauth: /capabilities/mcp/hotel using TUNIU_ACCESS_TOKEN
- api_key: /mcp/hotel using TUNIU_API_KEY (documented for CLI)
Neither mode registers an account, opens a browser, stores tokens, or retries.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import http.client
import os
from pathlib import Path
import ssl
import threading
import time
from zoneinfo import ZoneInfo

from .rollinggo_client import (
    RollingGoClient, RollingGoError, TransportResponse, _HTTPSBody,
)
from .tuniu_budget import DurableRequestGate

HOST = "openapi.tuniu.cn"
AUTH_PATHS = {
    "oauth": "/capabilities/mcp/hotel",
    "api_key": "/mcp/hotel",
}
READ_TOOLS = frozenset(("tuniuHotelSearch", "tuniuHotelDetail"))
ROOT = Path(__file__).resolve().parents[4]


class TuniuConfigurationError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _env_value(name: str, path: Path = ROOT / ".env") -> str:
    """Read one explicitly named project-local value; never return/log other secrets."""
    value = os.environ.get(name, "")
    if not value:
        try:
            if path.stat().st_size > 65536:
                raise TuniuConfigurationError("INVALID_CONFIGURATION")
            values = []
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                candidate = line.strip()
                if candidate.startswith("export "):
                    candidate = candidate[7:].strip()
                field, sep, content = candidate.partition("=")
                if sep and field.strip() == name:
                    content = content.strip()
                    if (len(content) >= 2 and content[0] == content[-1]
                            and content[0] in ('"', "'")):
                        content = content[1:-1]
                    values.append(content)
            if len(values) > 1:
                raise TuniuConfigurationError("INVALID_CONFIGURATION")
            value = values[0] if values else ""
        except FileNotFoundError:
            pass
        except (UnicodeError, OSError):
            raise TuniuConfigurationError("INVALID_CONFIGURATION") from None
    return value


def auth_configuration() -> tuple[str, str]:
    # A process override wins; otherwise honor the project-local ignored .env.
    # Do not source shell code or read global user profiles.
    mode = os.environ.get("XUNQI_TUNIU_AUTH_MODE")
    if mode is None:
        mode = _env_value("XUNQI_TUNIU_AUTH_MODE") or "oauth"
    mode = mode.strip()
    if mode not in AUTH_PATHS:
        raise TuniuConfigurationError("INVALID_CONFIGURATION")
    name = "TUNIU_ACCESS_TOKEN" if mode == "oauth" else "TUNIU_API_KEY"
    token = _env_value(name).strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or token in ("CHANGE_ME", "YOUR_TOKEN", "YOUR_API_KEY"):
        raise TuniuConfigurationError("CONFIGURATION_MISSING")
    if (
        len(token) > 4096 or not token.isascii()
        or any(ord(char) < 33 or ord(char) > 126 for char in token)
    ):
        raise TuniuConfigurationError("INVALID_CONFIGURATION")
    return mode, token


class RequestGate:
    """In-memory limiter for synthetic/offline test injection only.

    Production make_transport uses DurableRequestGate and a local SQLite ledger
    shared across this project's processes; external app usage remains unknown.
    """
    def __init__(self, clock=time.monotonic, wall_clock=None):
        self.lock = threading.Lock()
        self.clock = clock
        self.wall_clock = wall_clock or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self.recent = deque()
        self.today = None
        self.count = 0

    def reserve(self) -> None:
        with self.lock:
            now = self.clock()
            today = self.wall_clock().date()
            if today != self.today:
                self.today = today
                self.count = 0
            while self.recent and self.recent[0] <= now - 60:
                self.recent.popleft()
            if len(self.recent) >= 5:
                raise RollingGoError("RATE_LIMITED")
            if self.count >= 50:
                raise RollingGoError("REQUEST_LIMIT")
            self.recent.append(now)
            self.count += 1


SHARED_GATE = DurableRequestGate()


def make_transport(mode: str, *, gate: RequestGate | None = None):
    if mode not in AUTH_PATHS:
        raise TuniuConfigurationError("INVALID_CONFIGURATION")
    target_path = AUTH_PATHS[mode]
    budget = SHARED_GATE if gate is None else gate

    def send(body: bytes, headers: dict[str, str], timeout: float) -> TransportResponse:
        budget.reserve()
        request_headers = dict(headers)
        if mode == "api_key":
            # Tuniu official CLI uses the literal apiKey header for legacy MCP.
            # RollingGoClient emits Bearer; rewrite ONLY at this pinned host/path.
            authorization = request_headers.pop("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise RollingGoError("INVALID_CONFIGURATION")
            request_headers["apiKey"] = authorization.removeprefix("Bearer ")
        # http.client does not follow redirects or trust HTTP_PROXY settings.
        connection = http.client.HTTPSConnection(
            HOST, 443, timeout=timeout, context=ssl.create_default_context()
        )
        try:
            connection.request("POST", target_path, body=body, headers=request_headers)
            sock = connection.sock
            response = connection.getresponse()
            if sock is None:
                raise RollingGoError("TRANSPORT_ERROR")
            return TransportResponse(
                response.status, dict(response.getheaders()),
                _HTTPSBody(response, connection, sock),
            )
        except Exception:
            connection.close()
            raise
    return send


class TuniuMCPClient:
    """Only two read tool names can reach tools/call (never create_order)."""
    def __init__(self, *, transport=None, gate=None):
        mode, token = auth_configuration()
        self._client = RollingGoClient(
            token, timeout=15, total_timeout=55, max_requests=20,
            transport=transport or make_transport(mode, gate=gate),
            allowed_tools=READ_TOOLS,
        )

    @property
    def request_count(self) -> int:
        return self._client.request_count

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in READ_TOOLS:
            raise RollingGoError("TOOL_NOT_ALLOWED")
        return self._client.call_tool(name, arguments)

    def redact(self, data):
        return self._client.redact(data)

    def request_window(self, context):
        return self._client.request_window(context)
