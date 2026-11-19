"""Loopback ASGI boundary: origin, request size, cache headers and safe errors.

Security-sensitive; intentionally uses no supplier, session or business imports.
"""
from urllib.parse import urlsplit
import logging

from fastapi.responses import JSONResponse

logger = logging.getLogger("xunqi")


def error_response(code, message, status):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class LocalRequestGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        origin = headers.get(b"origin")
        if origin:
            try:
                parsed = urlsplit(origin.decode("latin1"))
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                serving_port = (scope.get("server") or ("", 8000))[1]
                allowed = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
                allowed = allowed and not parsed.username and not parsed.password
                allowed = allowed and port in (8000, 5173, serving_port)
                allowed = (
                    allowed
                    and parsed.path in ("", "/")
                    and not parsed.query
                    and not parsed.fragment
                )
            except ValueError:
                allowed = False
            if not allowed:
                return await error_response("ORIGIN_DENIED", "仅允许本机页面访问。", 403)(
                    scope, receive, send
                )
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 16384:
                return await error_response("REQUEST_TOO_LARGE", "请求内容过大。", 413)(
                    scope, receive, send
                )
            if not message.get("more_body"):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        started = False
        finished = False

        async def safe_send(message):
            nonlocal started, finished
            if message["type"] == "http.response.start":
                started = True
                if scope.get("path", "").startswith("/api/"):
                    message = {
                        **message,
                        "headers": [*message.get("headers", []), (b"cache-control", b"no-store")],
                    }
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        try:
            await self.app(scope, replay, safe_send)
        except Exception as error:
            # Contain before Uvicorn: its uncaught traceback could expose source exception text.
            logger.error("request_failed error_type=%s", type(error).__name__)
            if not started:
                await error_response("INTERNAL_ERROR", "查询暂时不可用，请稍后重试。", 500)(
                    scope, replay, safe_send
                )
            elif not finished:
                await safe_send({"type": "http.response.body", "body": b"", "more_body": False})
