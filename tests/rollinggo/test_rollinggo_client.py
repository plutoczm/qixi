"""Offline contract tests. No RollingGo endpoint is contacted."""
from collections import deque
import json
import ssl
import unittest
from unittest.mock import patch

from stay_agent.providers.data.rollinggo_client import (
    DEFAULT_PROTOCOL_VERSION,
    RollingGoClient,
    RollingGoError,
    TransportResponse,
    redact,
)

KEY = "test-only-api-key-7e1df9"
SESSION = "test-only-session-8ce512"


def json_reply(result, headers=None):
    def send(message, request_headers):
        return TransportResponse(200, {"Content-Type": "application/json", **(headers or {})},
            json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode())
    return send


def initialize(version=DEFAULT_PROTOCOL_VERSION, session=SESSION):
    return json_reply(
        {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": {"name": "offline-test", "version": "1"}},
        {} if session is None else {"Mcp-Session-Id": session},
    )


def notification(message, headers):
    if message != {"jsonrpc": "2.0", "method": "notifications/initialized"}:
        raise AssertionError("Incorrect initialized notification.")
    return TransportResponse(202, {}, b"")


def tools(names=("searchHotels", "getHotelDetail", "getHotelSearchTags")):
    return [{"name": name, "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}} for name in names]


class FakeTransport:
    def __init__(self, replies):
        self.replies = deque(replies)
        self.calls = []

    def __call__(self, body, headers, timeout):
        message = json.loads(body)
        self.calls.append((message, dict(headers), timeout))
        if not self.replies:
            raise AssertionError("Unexpected extra HTTP call.")
        reply = self.replies.popleft()
        return reply(message, headers) if callable(reply) else reply


class ChunkStream:
    def __init__(self, payload, chunk_size=3, fail_after_end=False, on_read=None):
        self.chunks = deque(payload[index:index + chunk_size] for index in range(0, len(payload), chunk_size))
        self.fail_after_end = fail_after_end
        self.on_read = on_read
        self.timeouts = []
        self.closed = False

    def read1(self, size):
        if self.on_read:
            self.on_read()
        if not self.chunks:
            if self.fail_after_end:
                raise AssertionError("Client waited for stream EOF after matching response.")
            return b""
        chunk = self.chunks.popleft()
        if len(chunk) > size:
            self.chunks.appendleft(chunk[size:])
            chunk = chunk[:size]
        return chunk

    def set_timeout(self, timeout):
        self.timeouts.append(timeout)

    def close(self):
        self.closed = True


class RollingGoClientTests(unittest.TestCase):
    def make_client(self, replies=None, **kwargs):
        transport = FakeTransport(replies or [initialize(), notification, json_reply({"tools": tools()})])
        return RollingGoClient(KEY, transport=transport, **kwargs), transport

    def assert_code(self, code, action):
        with self.assertRaises(RollingGoError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_json_handshake_headers_schema_and_readonly_call(self):
        raw_tools = tools() + [{"name": "createBooking", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]
        result = {"content": [{"type": "text", "text": "reference only"}], "structuredContent": {"reference_price": "188.00"}, "isError": False}
        client, transport = self.make_client([initialize(), notification, json_reply({"tools": raw_tools}), json_reply(result)])
        discovered = client.discover()
        self.assertEqual(discovered, raw_tools[:3])
        discovered[0]["inputSchema"]["type"] = "mutated-copy"
        self.assertEqual(client.discover()[0]["inputSchema"]["type"], "object")
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(client.call_tool("searchHotels", {"city": "\u5e7f\u5dde"}), result)
        self.assertEqual(client.request_count, 4)
        self.assertEqual(transport.calls[0][0]["params"]["protocolVersion"], DEFAULT_PROTOCOL_VERSION)
        self.assertNotIn("MCP-Protocol-Version", transport.calls[0][1])
        for message, headers, timeout in transport.calls[1:]:
            self.assertEqual(headers["MCP-Protocol-Version"], DEFAULT_PROTOCOL_VERSION)
            self.assertEqual(headers["Mcp-Session-Id"], SESSION)
            self.assertEqual(headers["Authorization"], "Bearer " + KEY)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 20)
            self.assertIn("application/json", headers["Accept"])
            self.assertIn("text/event-stream", headers["Accept"])
        self.assertEqual(transport.calls[-1][0]["params"], {"name": "searchHotels", "arguments": {"city": "\u5e7f\u5dde"}})

    def test_fragmented_sse_multiline_unicode_comments_and_matching_id(self):
        streams = []
        def sse(message, headers):
            progress = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
            unrelated = {"jsonrpc": "2.0", "id": 999, "result": {"ignore": True}}
            matched = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": "\u4e2d\u6587"}]}}
            lines = json.dumps(matched, ensure_ascii=False, indent=2).splitlines()
            payload = ": comment\r\nid: prime\r\ndata:\r\n\r\ndata: " + json.dumps(progress) + "\r\n\r\n"
            payload += "data: " + json.dumps(unrelated) + "\r\n\r\n"
            payload += "\r\n".join("data: " + line for line in lines) + "\r\n\r\n"
            stream = ChunkStream(payload.encode(), fail_after_end=True)
            streams.append(stream)
            return TransportResponse(200, {"Content-Type": "text/event-stream; charset=utf-8"}, stream)
        client, _ = self.make_client([initialize(), notification, json_reply({"tools": tools()}), sse])
        result = client.call_tool("getHotelDetail", {"hotelId": "opaque-id"})
        self.assertEqual(result["content"][0]["text"], "\u4e2d\u6587")
        self.assertTrue(streams[0].closed)
        self.assertTrue(streams[0].timeouts)

    def test_sse_line_endings_and_early_json_completion(self):
        for ending in ("\n", "\r", "\r\n"):
            def reply(message, headers, ending=ending):
                payload = "data: " + json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": tools()}}) + ending * 2
                return TransportResponse(200, {"content-type": "text/event-stream"}, ChunkStream(payload.encode(), fail_after_end=True))
            with self.subTest(ending=repr(ending)):
                client, _ = self.make_client([initialize(), notification, reply])
                self.assertEqual(len(client.discover()), 3)
        def open_json(message, headers):
            payload = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": tools()}}).encode()
            return TransportResponse(200, {"content-type": "application/json"}, ChunkStream(payload, fail_after_end=True))
        client, _ = self.make_client([initialize(), notification, open_json])
        self.assertEqual(len(client.discover()), 3)

    def test_json_wrong_response_id_or_boolean_id_is_rejected(self):
        for response_id in (999, True):
            bad = TransportResponse(200, {"Content-Type": "application/json"},
                json.dumps({"jsonrpc": "2.0", "id": response_id, "result": {}}).encode())
            client, _ = self.make_client([bad])
            self.assert_code("INVALID_RESPONSE", client.discover)

    def test_notification_must_be_202_with_empty_body(self):
        for status, body in ((200, b""), (202, b"unexpected")):
            client, _ = self.make_client([initialize(), TransportResponse(status, {}, body)])
            self.assert_code("INVALID_RESPONSE", client.discover)

    def test_supported_negotiated_versions_and_stateless_session(self):
        for version in ("2025-03-26", "2025-06-18", "2025-11-25"):
            client, transport = self.make_client([initialize(version, session=None), notification, json_reply({"tools": tools()})])
            self.assertEqual(len(client.discover()), 3)
            self.assertEqual(client.protocol_version, version)
            self.assertNotIn("Mcp-Session-Id", transport.calls[1][1])
            self.assertEqual(transport.calls[1][1]["MCP-Protocol-Version"], version)

    def test_unknown_modern_protocol_is_not_silently_accepted(self):
        client, transport = self.make_client([initialize("2026-07-28")])
        self.assert_code("UNSUPPORTED_PROTOCOL", client.discover)
        self.assertEqual(len(transport.calls), 1)

    def test_session_expires_without_automatic_tool_retry(self):
        client, transport = self.make_client([
            initialize(), notification, json_reply({"tools": tools()}),
            TransportResponse(404, {}, b"do not print"),
            initialize(session="new-session"), notification, json_reply({"tools": tools()}),
        ])
        client.discover()
        self.assert_code("SESSION_EXPIRED", lambda: client.call_tool("searchHotels", {}))
        self.assertEqual(len(transport.calls), 4)
        self.assertIsNone(client.protocol_version)
        self.assertEqual(len(client.discover()), 3)
        self.assertNotIn("Mcp-Session-Id", transport.calls[4][1])
        self.assertEqual(transport.calls[5][1]["Mcp-Session-Id"], "new-session")

    def test_builtin_https_preserves_tls_and_never_follows_redirect(self):
        with patch("stay_agent.providers.data.rollinggo_client.http.client.HTTPSConnection") as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.status = 302
            response.getheaders.return_value = [("Location", "https://unapproved.example/")]
            client = RollingGoClient(KEY)
            error = self.assert_code("REDIRECT_DENIED", client.discover)
            self.assertEqual(error.status, 302)
            self.assertEqual(factory.call_count, 1)
            args, kwargs = factory.call_args
            self.assertEqual(args, ("mcp.rollinggo.cn", 443))
            self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(kwargs["context"].check_hostname)
            request_args, request_kwargs = connection.request.call_args
            self.assertEqual(request_args, ("POST", "/mcp"))
            self.assertEqual(request_kwargs["headers"]["Authorization"], "Bearer " + KEY)
            self.assertNotIn("unapproved", str(error))

    def test_unlisted_or_write_tools_are_denied_before_any_http_request(self):
        for name in ("createBooking", "cancelBooking", "callRide", "searchHotels ", "SEARCHHOTELS", None):
            client, transport = self.make_client()
            self.assert_code("TOOL_NOT_ALLOWED", lambda: client.call_tool(name, {}))
            self.assertEqual(transport.calls, [])

    def test_missing_tool_and_invalid_arguments_do_not_trigger_tool_call(self):
        client, transport = self.make_client([initialize(), notification, json_reply({"tools": tools(("searchHotels",))})])
        self.assert_code("TOOL_NOT_AVAILABLE", lambda: client.call_tool("getHotelDetail", {}))
        self.assertEqual(len(transport.calls), 3)
        self.assert_code("INVALID_ARGUMENTS", lambda: client.call_tool("searchHotels", {"price": float("nan")}))
        self.assertEqual(len(transport.calls), 3)

    def test_request_budget_includes_handshake_and_notification(self):
        client, transport = self.make_client(max_requests=2)
        self.assert_code("REQUEST_LIMIT", client.discover)
        self.assertEqual(client.request_count, 2)
        self.assertEqual(len(transport.calls), 2)

    def test_response_and_sse_event_budgets_are_enforced(self):
        def oversized(message, headers):
            return TransportResponse(200, {"Content-Type": "application/json"}, b"x" * 65)
        with patch("stay_agent.providers.data.rollinggo_client.MAX_RESPONSE_BYTES", 64):
            client, _ = self.make_client([oversized])
            self.assert_code("RESPONSE_LIMIT", client.discover)
        def endless_events(message, headers):
            return TransportResponse(200, {"Content-Type": "text/event-stream"}, b": tick\n\n" * 4)
        with patch("stay_agent.providers.data.rollinggo_client.MAX_STREAM_EVENTS", 2):
            client, _ = self.make_client([endless_events])
            self.assert_code("RESPONSE_LIMIT", client.discover)

    def test_total_timeout_includes_transport_and_progress(self):
        clock = [100.0]
        def slow_transport(message, headers):
            clock[0] += 2
            return initialize()(message, headers)
        with patch("stay_agent.providers.data.rollinggo_client.time.monotonic", side_effect=lambda: clock[0]):
            client, _ = self.make_client([slow_transport], total_timeout=1)
            self.assert_code("TIMEOUT", client.discover)
        clock[0] = 100.0
        def chatter(message, headers):
            def advance():
                clock[0] += 0.3
            return TransportResponse(200, {"Content-Type": "text/event-stream"}, ChunkStream(b": tick\n\n" * 30, on_read=advance))
        with patch("stay_agent.providers.data.rollinggo_client.time.monotonic", side_effect=lambda: clock[0]):
            client, _ = self.make_client([chatter], total_timeout=1)
            self.assert_code("TIMEOUT", client.discover)

    def test_auth_http_rpc_and_transport_errors_do_not_include_secret_body_or_url(self):
        for status, code in ((401, "AUTHENTICATION_FAILED"), (403, "ACCESS_DENIED"), (429, "RATE_LIMITED"), (500, "HTTP_ERROR")):
            client, _ = self.make_client([TransportResponse(status, {}, (KEY + " https://private.example/").encode())])
            error = self.assert_code(code, client.discover)
            self.assertEqual(error.status, status)
            self.assertNotIn(KEY, str(error) + repr(error))
            self.assertNotIn("https", str(error))
        def rpc_error(message, headers):
            return TransportResponse(200, {"Content-Type": "application/json"},
                json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": KEY}}).encode())
        client, _ = self.make_client([rpc_error])
        self.assertNotIn(KEY, repr(self.assert_code("RPC_ERROR", client.discover)))
        def transport_error(message, headers):
            raise OSError(KEY + " https://private.example/")
        client, _ = self.make_client([transport_error])
        self.assertNotIn(KEY, repr(self.assert_code("TRANSPORT_ERROR", client.discover)))
        self.assertNotIn(KEY, repr(RollingGoError(KEY)))

    def test_tool_error_and_server_requests_are_not_executed(self):
        client, _ = self.make_client([initialize(), notification, json_reply({"tools": tools()}), json_reply({"isError": True, "content": [{"type": "text", "text": KEY}]})])
        self.assert_code("TOOL_ERROR", lambda: client.call_tool("searchHotels", {}))
        def server_request(message, headers):
            return TransportResponse(200, {"Content-Type": "text/event-stream"},
                b'data: {"jsonrpc":"2.0","id":99,"method":"sampling/createMessage","params":{}}\n\n')
        client, transport = self.make_client([server_request])
        self.assert_code("SERVER_REQUEST_UNSUPPORTED", client.discover)
        self.assertEqual(len(transport.calls), 1)

    def test_tool_pagination_is_bounded_and_duplicate_cursors_are_rejected(self):
        client, transport = self.make_client([
            initialize(), notification,
            json_reply({"tools": tools(("searchHotels",)), "nextCursor": "page-2"}),
            json_reply({"tools": tools(("getHotelDetail", "getHotelSearchTags"))}),
        ])
        self.assertEqual(len(client.discover()), 3)
        self.assertEqual(transport.calls[-1][0]["params"], {"cursor": "page-2"})
        client, _ = self.make_client([
            initialize(), notification,
            json_reply({"tools": [], "nextCursor": "same"}),
            json_reply({"tools": [], "nextCursor": "same"}),
        ])
        self.assert_code("INVALID_RESPONSE", client.discover)

    def test_recursive_redaction_and_safe_repr(self):
        client, _ = self.make_client()
        client.discover()
        original = {"api_key": KEY, "nested": [{"Mcp-Session-Id": SESSION, "message": "key=" + KEY + " session=" + SESSION}], "Authorization": "Bearer " + KEY, "plain": "Bearer unknown-token", "price": 188}
        sanitized = client.redact(original)
        serialized = json.dumps(sanitized)
        self.assertNotIn(KEY, serialized)
        self.assertNotIn(SESSION, serialized)
        self.assertNotIn("unknown-token", serialized)
        self.assertEqual(sanitized["price"], 188)
        self.assertEqual(original["api_key"], KEY)
        self.assertNotIn(KEY, repr(client))
        self.assertNotIn(SESSION, repr(client))
        self.assertNotIn(KEY, repr(TransportResponse(200, {"Authorization": KEY}, KEY.encode())))
        self.assertEqual(redact({"apiKey": "arbitrary", "nested": {"cookie": "secret"}}),
            {"apiKey": "[REDACTED]", "nested": {"cookie": "[REDACTED]"}})

    def test_invalid_configuration_is_safe_and_never_connects(self):
        for key in ("", KEY + "\n", None):
            with self.subTest(key_present=bool(key)):
                self.assert_code("INVALID_CONFIGURATION", lambda: RollingGoClient(key))
        for kwargs in ({"timeout": 0}, {"total_timeout": float("inf")}, {"max_requests": True}, {"max_requests": 33}):
            self.assert_code("INVALID_CONFIGURATION", lambda: RollingGoClient(KEY, **kwargs))


if __name__ == "__main__":
    unittest.main()
