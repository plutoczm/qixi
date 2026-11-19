"""Read-only FlyAI transport clients (direct MCP call or local CLI).

Both clients expose the same `call_tool(name, arguments)` interface and are
restricted to the single documented hotel-search tool. Neither retries, follows
a booking link, opens an order, or prints the API key. The direct MCP client
mirrors the published CLI request shape but stays unverified until a live
budget is approved; the CLI client is the transport already exercised.

The key travels only through process environment (CLI) or an Authorization
header (MCP). It must never appear in argv, logs, exceptions or Git.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
from collections.abc import Callable
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
from typing import Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

# This file lives at <project>/backend/stay_agent/providers/data/, so the
# project root is five levels up. Using parents[3] silently pointed at
# ``backend/``, which made both the git-ignored ``.env`` lookup and the vendored
# CLI path miss by one directory.
ROOT = Path(__file__).resolve().parents[4]
ENDPOINT = "https://flyai.open.fliggy.com/mcp"
READ_TOOLS = frozenset(("search_hotels",))
CLI_PATH = ROOT / "runtime/vendor/flyai-cli/node_modules/@fly-ai/flyai-cli/dist/flyai-bundle.cjs"
CLI_NETWORK_GUARD = ROOT / "tools" / "flyai-one-request.cjs"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_CODES = frozenset((
    "INVALID_CONFIGURATION", "INVALID_ARGUMENTS", "TOOL_NOT_ALLOWED",
    "TOOL_NOT_AVAILABLE", "REQUEST_LIMIT", "AUTHENTICATION_FAILED",
    "ACCESS_DENIED", "RATE_LIMITED", "TIMEOUT", "TRANSPORT_ERROR",
    "HTTP_ERROR", "REDIRECT_DENIED", "INVALID_RESPONSE", "RPC_ERROR",
    "SOURCE_ERROR", "RESPONSE_LIMIT",
))
# Canonical MCP argument names -> CLI flags used by the official bundle.
CLI_FLAGS = {
    "destName": "--dest-name",
    "keyWords": "--key-words",
    "poiName": "--poi-name",
    "hotelTypes": "--hotel-types",
    "sort": "--sort",
    "checkInDate": "--check-in-date",
    "checkOutDate": "--check-out-date",
    "hotelStars": "--hotel-stars",
    "hotelBedTypes": "--hotel-bed-types",
    "maxPrice": "--max-price",
}


class FlyAIError(Exception):
    """Safe classification only: no key, body, URL or supplier message."""

    def __init__(self, code: str, status: int | None = None):
        self.code = code if code in MAX_ERROR_CODES else "TRANSPORT_ERROR"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(self.code if self.status is None else f"{self.code} (HTTP {self.status})")


@dataclass(frozen=True)
class TransportResponse:
    status: int
    content_type: str
    body: bytes


def load_key() -> str:
    """Process environment wins; otherwise the git-ignored project .env."""
    value = os.environ.get("FLYAI_API_KEY", "").strip()
    if value:
        return value
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, rest = line.partition("=")
        if name.strip() == "FLYAI_API_KEY":
            return rest.strip().strip('"').strip("'")
    return ""


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FlyAIError("REDIRECT_DENIED", code)


def https_transport(url: str, headers: Mapping[str, str], body: bytes,
                    timeout: float) -> TransportResponse:
    request = urlrequest.Request(url, data=body, headers=dict(headers), method="POST")
    opener = urlrequest.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise FlyAIError("RESPONSE_LIMIT")
            return TransportResponse(
                response.status, response.headers.get("Content-Type", ""), payload,
            )
    except urlerror.HTTPError as failure:
        raise FlyAIError(_http_code(failure.code), failure.code) from None
    except (urlerror.URLError, TimeoutError, OSError, ValueError):
        raise FlyAIError("TRANSPORT_ERROR") from None


def _http_code(status: int) -> str:
    if status in (401, 403):
        return "AUTHENTICATION_FAILED"
    if status == 429:
        return "RATE_LIMITED"
    return "HTTP_ERROR"


def _sign(secret: str, method: str, path: str, timestamp: str,
          nonce: str, body: str, authorization: str) -> str:
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    payload = "\n".join((method, path, timestamp, nonce, digest(body),
                         digest(authorization)))
    return base64.urlsafe_b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                 hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")


def _unwrap(response: Mapping) -> Mapping:
    if response.get("error"):
        raise FlyAIError("RPC_ERROR")
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise FlyAIError("INVALID_RESPONSE")
    if result.get("isError") is True:
        raise FlyAIError("SOURCE_ERROR")
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, Mapping) and isinstance(first.get("text"), str):
            text = first["text"].strip()
            if text:
                try:
                    parsed = json.loads(text)
                except ValueError:
                    raise FlyAIError("INVALID_RESPONSE") from None
                if isinstance(parsed, Mapping):
                    return parsed
                raise FlyAIError("INVALID_RESPONSE")
    raise FlyAIError("INVALID_RESPONSE")


def _parse_body(response: TransportResponse) -> Mapping:
    body = response.body
    if "text/event-stream" in response.content_type.lower():
        found = None
        events = 0
        for line in body.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            events += 1
            if events > 256:
                raise FlyAIError("RESPONSE_LIMIT")
            try:
                message = json.loads(data)
            except ValueError:
                continue
            if isinstance(message, Mapping) and message.get("error"):
                raise FlyAIError("RPC_ERROR")
            if isinstance(message, Mapping) and "result" in message:
                found = message
        if found is None:
            raise FlyAIError("INVALID_RESPONSE")
        return found
    try:
        message = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FlyAIError("INVALID_RESPONSE") from None
    if not isinstance(message, Mapping):
        raise FlyAIError("INVALID_RESPONSE")
    return message


class FlyAIMCPClient:
    """One JSON-RPC tools/call per request; no handshake, retry or redirect."""

    def __init__(self, key: str, *, transport=None, timeout: float = 30.0,
                 max_requests: int = 5, sign_secret: str | None = None,
                 endpoint: str = ENDPOINT):
        if not key or not key.strip():
            raise FlyAIError("INVALID_CONFIGURATION")
        if type(max_requests) is not int or max_requests < 1:
            raise FlyAIError("INVALID_ARGUMENTS")
        self._key = key.strip()
        self.endpoint = endpoint
        self.transport = transport or https_transport
        self.timeout = timeout
        self.max_requests = max_requests
        self.sign_secret = (sign_secret or "").strip() or None
        self.requests = 0

    def call_tool(self, name: str, arguments: Mapping) -> Mapping:
        if name not in READ_TOOLS:
            raise FlyAIError("TOOL_NOT_ALLOWED")
        if not isinstance(arguments, Mapping) or "destName" not in arguments:
            raise FlyAIError("INVALID_ARGUMENTS")
        if self.requests >= self.max_requests:
            raise FlyAIError("REQUEST_LIMIT")
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments)},
        }, ensure_ascii=False, separators=(",", ":"))
        authorization = "Bearer " + self._key
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": authorization,
            "User-Agent": "xunqi-flyai/0.1 (personal research)",
        }
        if self.sign_secret:
            timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
            nonce = secrets.token_hex(16)
            path = "/" + self.endpoint.split("/", 3)[3] if self.endpoint.count("/") >= 3 else "/"
            headers.update({
                "x-flyai-ts": timestamp,
                "x-flyai-nonce": nonce,
                "x-flyai-sign-ver": "7",
                "x-flyai-sign-alg": "hmac-sha256",
                "x-flyai-sign": _sign(
                    self.sign_secret, "POST", path, timestamp, nonce,
                    body, authorization,
                ),
            })
        self.requests += 1
        response = self.transport(self.endpoint, headers, body.encode("utf-8"),
                                  self.timeout)
        if not isinstance(response, TransportResponse):
            raise FlyAIError("TRANSPORT_ERROR")
        if response.status >= 400:
            raise FlyAIError(_http_code(response.status), response.status)
        payload = _unwrap(_parse_body(response))
        status = payload.get("status")
        if type(status) is not int:
            raise FlyAIError("INVALID_RESPONSE")
        if status != 0:
            raise FlyAIError("SOURCE_ERROR")
        return payload


class FlyAICliClient:
    """Bounded wrapper around the installed official bundle.

    The bundle exits with 0xC0000409 on Windows after printing valid JSON, so a
    non-zero code is tolerated only when the last stdout line parses as JSON.
    """

    def __init__(self, key: str | None, *, cli_path: Path | None = None,
                 timeout: float = 90.0, max_requests: int = 5, guard_network: bool = False,
                 runner: Callable[[list[str], Mapping[str, str], float],
                                  tuple[int, str, str]] | None = None):
        # An empty key is **not** an error. The official bundle resolves its own
        # credential chain (FLYAI_API_KEY -> DEBUG_FLYAI_API_KEY ->
        # ~/.flyai/config.json -> built-in default), so refusing to run here
        # would block a channel that works. What the empty key *does* mean is
        # that the credential's owner is unproven, which is recorded separately
        # and must never be reported as the user's own quota.
        if type(max_requests) is not int or max_requests < 1:
            raise FlyAIError("INVALID_ARGUMENTS")
        self._key = (key or "").strip()
        #: ``project_env`` = the project supplied a key; ``cli_default_chain`` =
        #: no project key, so the bundle fell back on its own chain and the
        #: credential owner is unproven.
        self.credential_source = "project_env" if self._key else "cli_default_chain"
        self.cli_path = cli_path or CLI_PATH
        self.timeout = timeout
        self.max_requests = max_requests
        self.guard_network = guard_network
        self.runner = runner or _run_cli
        self._check_cli = runner is None
        self.requests = 0

    def call_tool(self, name: str, arguments: Mapping) -> Mapping:
        if name not in READ_TOOLS:
            raise FlyAIError("TOOL_NOT_ALLOWED")
        if not isinstance(arguments, Mapping) or "destName" not in arguments:
            raise FlyAIError("INVALID_ARGUMENTS")
        if self.requests >= self.max_requests:
            raise FlyAIError("REQUEST_LIMIT")
        if self._check_cli and not self.cli_path.exists():
            raise FlyAIError("INVALID_CONFIGURATION")
        command = ["node"]
        if self.guard_network:
            command.extend(("--require", str(CLI_NETWORK_GUARD)))
        command.extend((str(self.cli_path), "search-hotel"))
        for key, value in arguments.items():
            flag = CLI_FLAGS.get(key)
            if flag is None:
                raise FlyAIError("INVALID_ARGUMENTS")
            command.extend((flag, str(value)))
        env = dict(os.environ)
        if self._key:
            env["FLYAI_API_KEY"] = self._key
        else:
            # Never inherit a stray key from the parent process: that would make
            # the recorded credential source a lie.
            env.pop("FLYAI_API_KEY", None)
        for name_to_drop in ("DEBUG_FLYAI_API_KEY", "DEBUG_FLYAI_MCP_URL"):
            env.pop(name_to_drop, None)
        if self.guard_network:
            env.pop("NODE_OPTIONS", None)
        self.requests += 1
        try:
            code, stdout, _ = self.runner(command, env, self.timeout)
        except subprocess.TimeoutExpired:
            raise FlyAIError("TIMEOUT") from None
        except OSError:
            raise FlyAIError("TRANSPORT_ERROR") from None
        lines = [line for line in (stdout or "").splitlines() if line.strip()]
        if not lines:
            raise FlyAIError("INVALID_RESPONSE")
        try:
            payload = json.loads(lines[-1])
        except ValueError:
            raise FlyAIError("INVALID_RESPONSE") from None
        if not isinstance(payload, Mapping):
            raise FlyAIError("INVALID_RESPONSE")
        status = payload.get("status")
        if type(status) is not int:
            # The CLI also prints {"tool": ..., "arguments": ...} when it has
            # no endpoint; never treat that dry-run shape as a source result.
            raise FlyAIError("INVALID_RESPONSE")
        if status != 0:
            raise FlyAIError("SOURCE_ERROR")
        return payload


def _run_cli(command: list[str], env: Mapping[str, str],
             timeout: float) -> tuple[int, str, str]:
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, env=dict(env), cwd=str(ROOT),
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def search_callback(client) -> Callable[[Mapping], Mapping]:
    """Adapt either client to the FlyAIDiscoveryProvider callback contract."""
    return lambda arguments: client.call_tool("search_hotels", arguments)
