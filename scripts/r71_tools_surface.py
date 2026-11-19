"""r71: enumerate the FULL tools/list surface of the configured Tuniu MCP account.

Unlike verify_tuniu_readonly.py, which filters discovery through the project's
two-tool read allowlist, this probe prints every tool the account advertises
plus its top-level input field names. That is what lets us answer "is there an
un-wired read interface?" without guessing from documentation.

Default invocation sends ZERO provider HTTP. --live allows exactly three raw
attempts (initialize, notifications/initialized, tools/list), no tools/call,
no pagination, no retries. Requests go through the shared durable ledger, so
they are counted like every other real attempt. Only a redacted projection is
printed: tool names, description length, input field names, required fields.
No raw response, key, token, session id or hotel payload is retained.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.providers.data.rollinggo_client import (  # noqa: E402
    RollingGoClient, RollingGoError,
)
from stay_agent.providers.data.tuniu_mcp import (  # noqa: E402
    TuniuConfigurationError, auth_configuration, make_transport,
)

_MAX_RAW = 3
_ALLOWED_METHODS = frozenset(
    ("initialize", "notifications/initialized", "tools/list")
)


def _project(tool: dict) -> dict:
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = {}
    props = schema.get("properties")
    required = schema.get("required")
    return {
        "name": tool["name"],
        "description_chars": len(str(tool.get("description", ""))),
        "input_fields": sorted(props.keys()) if isinstance(props, dict) else [],
        "required_fields": (
            sorted(str(item) for item in required) if isinstance(required, list) else []
        ),
    }


def run(*, live: bool) -> dict:
    outcome: dict = {
        "provider": "tuniu",
        "live_requested": live,
        "raw_http_attempts": 0,
        "status": "NOT_RUN",
    }
    if not live:
        outcome["status"] = "LOCAL_ONLY_NO_HTTP"
        return outcome
    try:
        mode, token = auth_configuration()
    except TuniuConfigurationError as exc:
        outcome["status"] = exc.code
        return outcome
    outcome["auth_mode"] = mode
    if mode != "api_key":
        outcome["status"] = "AUTH_MODE_NOT_API_KEY"
        return outcome

    attempts = 0
    raw_transport = make_transport(mode)

    def bounded(body: bytes, headers: dict, timeout: float):
        nonlocal attempts
        try:
            method = json.loads(body).get("method")
        except (ValueError, TypeError, AttributeError):
            raise RollingGoError("INVALID_ARGUMENTS") from None
        if method not in _ALLOWED_METHODS:
            raise RollingGoError("TOOL_NOT_ALLOWED")
        if attempts >= _MAX_RAW:
            raise RollingGoError("REQUEST_LIMIT")
        attempts += 1
        return raw_transport(body, headers, timeout)

    try:
        client = RollingGoClient(
            token, timeout=15, total_timeout=45, max_requests=4,
            transport=bounded,
            # A sentinel keeps the client's own allowlist from filtering the
            # discovery response; we never call tools/call in this probe.
            allowed_tools=frozenset({"__probe_sentinel__"}),
        )
        client._initialize()
        result, _ = client._rpc("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise RollingGoError("INVALID_RESPONSE")
        rows = []
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                raise RollingGoError("INVALID_RESPONSE")
            rows.append(_project(tool))
        outcome["tools"] = rows
        outcome["next_cursor_present"] = result.get("nextCursor") is not None
        outcome["status"] = "OK"
    except RollingGoError as exc:
        outcome["status"] = exc.code
    except Exception:
        outcome["status"] = "CLIENT_ERROR"
    finally:
        outcome["raw_http_attempts"] = attempts
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow 3 real HTTP attempts")
    parser.add_argument("--out", type=Path, help="write the projected JSON here")
    args = parser.parse_args()
    result = run(live=args.live)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0 if result["status"] in ("OK", "LOCAL_ONLY_NO_HTTP") else 1


if __name__ == "__main__":
    raise SystemExit(main())
