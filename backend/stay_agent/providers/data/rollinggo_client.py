"""Small read-only RollingGo MCP client (stdlib only; no implicit calls).

Default handshake revision: 2025-06-18. Supported handshake revisions are
2025-03-26, 2025-06-18, and 2025-11-25; 2026-era protocols are not implemented.
The client never loads key files, prints responses, follows redirects, or retries
tool calls. A supplied transport must obey its timeout and expose a bounded,
timeout-aware binary body, as the built-in HTTPS transport does.
"""
from __future__ import annotations

from contextlib import contextmanager

from dataclasses import dataclass
import copy
import http.client
import io
import json
import math
import re
import socket
import ssl
import time
from typing import Any, BinaryIO, Callable, Mapping

ENDPOINT = "https://mcp.rollinggo.cn/mcp"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(("2025-03-26", "2025-06-18", "2025-11-25"))
READ_ONLY_TOOLS = frozenset(("searchHotels", "getHotelDetail", "getHotelSearchTags"))
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_STREAM_EVENTS = 256

_ERROR_CODES = frozenset((
    "INVALID_CONFIGURATION", "INVALID_ARGUMENTS", "AUTHENTICATION_FAILED",
    "ACCESS_DENIED", "RATE_LIMITED", "SESSION_EXPIRED", "REDIRECT_DENIED",
    "HTTP_ERROR", "TIMEOUT", "TRANSPORT_ERROR", "INVALID_RESPONSE",
    "RPC_ERROR", "TOOL_ERROR", "UNSUPPORTED_PROTOCOL", "UNSUPPORTED_CAPABILITY",
    "SERVER_REQUEST_UNSUPPORTED", "TOOL_NOT_ALLOWED", "TOOL_NOT_AVAILABLE",
    "REQUEST_LIMIT", "RESPONSE_LIMIT", "CLIENT_ERROR", "CANCELLED",
))


class RollingGoError(Exception):
    """Safe classification only: no server message, URL, token, or response body."""

    def __init__(self, code: str, status: int | None = None):
        self.code = code if isinstance(code, str) and code in _ERROR_CODES else "CLIENT_ERROR"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(self.code if self.status is None else f"{self.code} (HTTP {self.status})")


_SENSITIVE_FIELDS = frozenset((
    "key", "apikey", "authorization", "proxyauthorization", "token",
    "accesstoken", "refreshtoken", "password", "secret", "cookie",
    "setcookie", "session", "sessionid", "mcpsessionid", "credentials",
))
_BEARER = re.compile(r"\bBearer\s+[^\s\"'<>;,]+", re.IGNORECASE)


def redact(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """Copy JSON-like data while masking sensitive fields and known secrets.

    Module-level use recognizes field names and Bearer values. client.redact(...)
    additionally masks the exact API key and all session IDs seen by that client.
    Arbitrary bare strings cannot be identified as unknown secrets automatically.
    """
    known = sorted({item for item in secrets if isinstance(item, str) and item}, key=len, reverse=True)

    def scrub(text: str) -> str:
        for secret in known:
            text = text.replace(secret, "[REDACTED]")
        return _BEARER.sub("Bearer [REDACTED]", text)

    def visit(item: Any, depth: int) -> Any:
        if depth > 30:
            return "[TRUNCATED]"
        if isinstance(item, dict):
            result = {}
            for key, entry in item.items():
                name = key if isinstance(key, str) else "[NON_STRING_KEY]"
                normalized = re.sub(r"[^a-z0-9]", "", name.lower())
                sensitive = normalized in _SENSITIVE_FIELDS or normalized.endswith(("apikey", "password", "secret", "sessionid"))
                result[scrub(name)] = "[REDACTED]" if sensitive else visit(entry, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [visit(entry, depth + 1) for entry in item]
        if isinstance(item, str):
            return scrub(item)
        if item is None or type(item) in (int, float, bool):
            return item
        return "[UNSUPPORTED_VALUE]"

    return visit(value, 0)


@dataclass(repr=False)
class TransportResponse:
    """Transport return value; body may be bytes or a timeout-aware binary stream."""

    status: int
    headers: Mapping[str, str]
    body: BinaryIO | bytes

    def close(self) -> None:
        closer = getattr(self.body, "close", None)
        if callable(closer):
            closer()


class _HTTPSBody:
    def __init__(self, response, connection, request_socket):
        self._response = response
        self._connection = connection
        self._socket = request_socket

    def set_timeout(self, timeout: float) -> None:
        self._socket.settimeout(timeout)

    def read1(self, size: int) -> bytes:
        return self._response.read1(size)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


def _https_transport(body: bytes, headers: dict[str, str], timeout: float) -> TransportResponse:
    # http.client does not follow redirects or implicitly consume proxy settings.
    connection = http.client.HTTPSConnection(
        "mcp.rollinggo.cn", 443, timeout=timeout, context=ssl.create_default_context()
    )
    try:
        connection.request("POST", "/mcp", body=body, headers=headers)
        request_socket = connection.sock
        response = connection.getresponse()
        if request_socket is None:
            raise RollingGoError("TRANSPORT_ERROR")
        return TransportResponse(
            response.status, dict(response.getheaders()),
            _HTTPSBody(response, connection, request_socket),
        )
    except Exception:
        connection.close()
        raise


Transport = Callable[[bytes, dict[str, str], float], TransportResponse]


def _strict_json(text: str) -> Any:
    def reject_constant(value):
        raise ValueError
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=object_pairs)


class RollingGoClient:
    def __init__(
        self, api_key: str, timeout: float = 20, total_timeout: float = 90,
        max_requests: int = 8, transport: Transport | None = None,
        allowed_tools: frozenset[str] | None = None,
    ):
        if (
            not isinstance(api_key, str) or not api_key or len(api_key) > 4096
            or any(ord(char) < 0x21 or ord(char) > 0x7e for char in api_key)
        ):
            raise RollingGoError("INVALID_CONFIGURATION")
        for value in (timeout, total_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise RollingGoError("INVALID_CONFIGURATION")
        if type(max_requests) is not int or not 1 <= max_requests <= 32:
            raise RollingGoError("INVALID_CONFIGURATION")
        if transport is not None and not callable(transport):
            raise RollingGoError("INVALID_CONFIGURATION")
        self._api_key = api_key
        self._timeout = float(timeout)
        self._total_timeout = float(total_timeout)
        self._max_requests = max_requests
        if allowed_tools is not None and (
            not isinstance(allowed_tools, frozenset)
            or not allowed_tools
            or any(type(name) is not str or not name or len(name) > 90 for name in allowed_tools)
        ):
            raise RollingGoError("INVALID_CONFIGURATION")
        self._allowed_tools = READ_ONLY_TOOLS if allowed_tools is None else allowed_tools
        self._transport = transport or _https_transport
        self._deadline: float | None = None
        self._request_count = 0
        self._next_id = 1
        self._protocol_version: str | None = None
        self._session_id: str | None = None
        self._redaction_secrets = [api_key]
        self._tools: list[dict] | None = None

    def __repr__(self) -> str:
        return f"RollingGoClient(protocol_version={self._protocol_version!r}, request_count={self._request_count})"

    @property
    def protocol_version(self) -> str | None:
        return self._protocol_version

    @property
    def request_count(self) -> int:
        return self._request_count

    def redact(self, value: Any) -> Any:
        return redact(value, secrets=tuple(self._redaction_secrets))

    def limit_deadline(self, deadline: float) -> None:
        """Narrow this client's total monotonic deadline, never extend it."""
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise RollingGoError("INVALID_CONFIGURATION")
        self._deadline = min(self._deadline if self._deadline is not None else (time.monotonic() + self._total_timeout), deadline)

    @contextmanager
    def request_window(self, context):
        previous = self._deadline, getattr(self, "cancelled", None)
        self.limit_deadline(context.deadline)
        self.cancelled = context.cancelled
        self._reserve = context.spend
        try:
            yield
        finally:
            self._deadline, self.cancelled = previous
            self._reserve = None

    def _remaining(self) -> float:
        if getattr(self, "cancelled", None) is not None and self.cancelled.is_set():
            raise RollingGoError("CANCELLED")
        if self._deadline is None:
            self._deadline = time.monotonic() + self._total_timeout
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise RollingGoError("TIMEOUT")
        return min(self._timeout, remaining)

    def _read_chunk(self, body, remaining_bytes: int) -> bytes:
        limit = min(4096, remaining_bytes + 1)
        timeout = self._remaining()
        setter = getattr(body, "set_timeout", None)
        if callable(setter):
            setter(timeout)
        reader = getattr(body, "read1", None)
        chunk = reader(limit) if callable(reader) else body.read(1)
        self._remaining()
        if not isinstance(chunk, bytes):
            raise RollingGoError("INVALID_RESPONSE")
        if len(chunk) > remaining_bytes:
            raise RollingGoError("RESPONSE_LIMIT")
        return chunk

    def _matched_result(self, message: Any, request_id: int, *, stream: bool) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RollingGoError("INVALID_RESPONSE")
        if "method" in message:
            if "id" in message:
                raise RollingGoError("SERVER_REQUEST_UNSUPPORTED")
            if not isinstance(message["method"], str):
                raise RollingGoError("INVALID_RESPONSE")
            if stream:
                return None
            raise RollingGoError("INVALID_RESPONSE")
        if type(message.get("id")) is not int or message["id"] != request_id:
            if stream:
                return None
            raise RollingGoError("INVALID_RESPONSE")
        if ("result" in message) == ("error" in message):
            raise RollingGoError("INVALID_RESPONSE")
        if "error" in message:
            raise RollingGoError("RPC_ERROR")
        if not isinstance(message["result"], dict):
            raise RollingGoError("INVALID_RESPONSE")
        return message["result"]

    def _read_json(self, body, request_id: int) -> dict:
        data = bytearray()
        while True:
            chunk = self._read_chunk(body, MAX_RESPONSE_BYTES - len(data))
            if not chunk:
                raise RollingGoError("INVALID_RESPONSE")
            data.extend(chunk)
            try:
                message = _strict_json(data.decode("utf-8"))
            except (ValueError, UnicodeError):
                continue
            # A complete matching object ends the read; never wait for a live stream EOF.
            result = self._matched_result(message, request_id, stream=False)
            assert result is not None
            return result

    def _sse_lines(self, body):
        line = bytearray()
        size = 0
        skip_lf = False
        first = True
        while True:
            chunk = self._read_chunk(body, MAX_RESPONSE_BYTES - size)
            if not chunk:
                return
            size += len(chunk)
            for byte in chunk:
                if skip_lf:
                    skip_lf = False
                    if byte == 10:
                        continue
                if byte in (10, 13):
                    text = bytes(line).decode("utf-8")
                    line.clear()
                    if first:
                        text = text.removeprefix("\ufeff")
                        first = False
                    if byte == 13:
                        skip_lf = True
                    yield text
                else:
                    line.append(byte)

    def _read_sse(self, body, request_id: int) -> dict:
        data_lines: list[str] = []
        events = 0
        for line in self._sse_lines(body):
            if line == "":
                events += 1
                if events > MAX_STREAM_EVENTS:
                    raise RollingGoError("RESPONSE_LIMIT")
                if not data_lines:
                    continue
                payload = "\n".join(data_lines)
                data_lines.clear()
                if not payload.strip():
                    continue
                try:
                    message = _strict_json(payload)
                except (ValueError, UnicodeError):
                    raise RollingGoError("INVALID_RESPONSE") from None
                result = self._matched_result(message, request_id, stream=True)
                if result is not None:
                    return result
            elif not line.startswith(":"):
                field, separator, value = line.partition(":")
                if field == "data":
                    data_lines.append(value.removeprefix(" ") if separator else "")
        raise RollingGoError("INVALID_RESPONSE")

    def _exchange(self, payload: dict, request_id: int | None) -> tuple[dict | None, dict[str, str]]:
        if self._request_count >= self._max_requests:
            raise RollingGoError("REQUEST_LIMIT")
        try:
            wire = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise RollingGoError("INVALID_ARGUMENTS") from None
        headers = {
            "Authorization": "Bearer " + self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
        }
        if self._protocol_version is not None:
            headers["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        response = None
        try:
            timeout = self._remaining()
            if getattr(self, "_reserve", None) is not None and not self._reserve():
                raise RollingGoError("REQUEST_LIMIT")
            self._request_count += 1
            response = self._transport(wire, headers, timeout)
            self._remaining()
            if not isinstance(response, TransportResponse) or type(response.status) is not int:
                raise RollingGoError("INVALID_RESPONSE")
            status = response.status
            if 300 <= status <= 399:
                raise RollingGoError("REDIRECT_DENIED", status)
            if status < 200 or status > 299:
                codes = {401: "AUTHENTICATION_FAILED", 403: "ACCESS_DENIED", 429: "RATE_LIMITED"}
                if status == 404 and self._session_id is not None:
                    self._session_id = None
                    self._protocol_version = None
                    self._tools = None
                    raise RollingGoError("SESSION_EXPIRED", status)
                raise RollingGoError(codes.get(status, "HTTP_ERROR"), status)
            normalized = {key.lower(): value for key, value in response.headers.items()}
            body = io.BytesIO(response.body) if isinstance(response.body, bytes) else response.body
            if request_id is None:
                if status != 202 or self._read_chunk(body, MAX_RESPONSE_BYTES):
                    raise RollingGoError("INVALID_RESPONSE")
                return None, normalized
            if status == 202:
                raise RollingGoError("INVALID_RESPONSE")
            if normalized.get("content-encoding", "identity").lower() != "identity":
                raise RollingGoError("INVALID_RESPONSE")
            content_type = normalized.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/json":
                result = self._read_json(body, request_id)
            elif content_type == "text/event-stream":
                result = self._read_sse(body, request_id)
            else:
                raise RollingGoError("INVALID_RESPONSE")
            return result, normalized
        except RollingGoError:
            raise
        except (TimeoutError, socket.timeout):
            raise RollingGoError("TIMEOUT") from None
        except Exception:
            raise RollingGoError("TRANSPORT_ERROR") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _rpc(self, method: str, params: dict) -> tuple[dict, dict[str, str]]:
        request_id = self._next_id
        self._next_id += 1
        result, headers = self._exchange(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            request_id,
        )
        assert result is not None
        return result, headers

    def _initialize(self) -> None:
        self._protocol_version = None
        self._session_id = None
        result, headers = self._rpc("initialize", {
            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "xunqi-readonly-probe", "version": "0.1.0"},
        })
        version = result.get("protocolVersion")
        if not isinstance(version, str) or version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise RollingGoError("UNSUPPORTED_PROTOCOL")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
            raise RollingGoError("UNSUPPORTED_CAPABILITY")
        session_id = headers.get("mcp-session-id")
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id or len(session_id) > 4096 or any(
                ord(char) < 0x21 or ord(char) > 0x7e for char in session_id
            ):
                raise RollingGoError("INVALID_RESPONSE")
            self._redaction_secrets.append(session_id)
        self._protocol_version = version
        self._session_id = session_id
        self._exchange({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)

    def discover(self) -> list[dict]:
        """Initialize, list bounded pages, and return only allowlisted raw schemas."""
        if self._tools is not None:
            return copy.deepcopy(self._tools)
        self._initialize()
        collected: list[dict] = []
        seen_names: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = None
        while True:
            result, _ = self._rpc("tools/list", {} if cursor is None else {"cursor": cursor})
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise RollingGoError("INVALID_RESPONSE")
            for tool in tools:
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                    raise RollingGoError("INVALID_RESPONSE")
                name = tool["name"]
                if name in seen_names:
                    raise RollingGoError("INVALID_RESPONSE")
                seen_names.add(name)
                if name in self._allowed_tools:
                    if not isinstance(tool.get("inputSchema"), dict):
                        raise RollingGoError("INVALID_RESPONSE")
                    collected.append(tool)
            cursor = result.get("nextCursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise RollingGoError("INVALID_RESPONSE")
            seen_cursors.add(cursor)
        self._tools = collected
        return copy.deepcopy(collected)

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Invoke one of three locally allowlisted tools, never another MCP action."""
        if not isinstance(name, str) or name not in self._allowed_tools:
            raise RollingGoError("TOOL_NOT_ALLOWED")
        if not isinstance(arguments, dict):
            raise RollingGoError("INVALID_ARGUMENTS")
        available = self.discover()
        if name not in {tool["name"] for tool in available}:
            raise RollingGoError("TOOL_NOT_AVAILABLE")
        result, _ = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            raise RollingGoError("TOOL_ERROR")
        if "isError" in result and type(result["isError"]) is not bool:
            raise RollingGoError("INVALID_RESPONSE")
        return result
