"""Offline checks for the FlyAI direct-MCP and CLI transports.

No key, network, subprocess or supplier request is used: transports and
runners are explicit fakes.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from stay_agent.providers.data import flyai_client as module
from stay_agent.providers.data.flyai_client import (
    CLI_NETWORK_GUARD, FlyAIError, FlyAICliClient, FlyAIMCPClient, TransportResponse,
    load_key, search_callback,
)

PAYLOAD = {"status": 0, "data": {"itemList": [{"name": "模拟酒店"}]}}


def reply(content=PAYLOAD, *, is_error=False):
    return TransportResponse(
        200, "application/json",
        json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}],
                "isError": is_error,
            },
        }).encode("utf-8"),
    )


class Recorder:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or reply()

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), body, timeout))
        return self.response


def test_mcp_client_sends_one_readonly_tool_call_without_leaking_key():
    recorder = Recorder()
    client = FlyAIMCPClient("secret-key-value", transport=recorder)
    result = client.call_tool("search_hotels", {"destName": "广州", "maxPrice": 200})
    assert result == PAYLOAD
    url, headers, body, _ = recorder.calls[0]
    assert url == module.ENDPOINT
    assert headers["Authorization"] == "Bearer secret-key-value"
    assert "x-flyai-sign" not in headers
    payload = json.loads(body)
    assert payload["method"] == "tools/call"
    assert payload["params"] == {
        "name": "search_hotels", "arguments": {"destName": "广州", "maxPrice": 200},
    }
    assert "secret-key-value" not in body.decode("utf-8")
    assert client.requests == 1


def test_mcp_client_rejects_other_tools_and_non_destination_arguments():
    client = FlyAIMCPClient("key", transport=Recorder())
    with pytest.raises(FlyAIError, match="TOOL_NOT_ALLOWED"):
        client.call_tool("create_order", {"destName": "广州"})
    with pytest.raises(FlyAIError, match="INVALID_ARGUMENTS"):
        client.call_tool("search_hotels", {"maxPrice": 200})
    assert client.requests == 0


def test_mcp_client_budget_is_consumed_before_send():
    recorder = Recorder()
    client = FlyAIMCPClient("key", transport=recorder, max_requests=1)
    client.call_tool("search_hotels", {"destName": "广州"})
    with pytest.raises(FlyAIError, match="REQUEST_LIMIT"):
        client.call_tool("search_hotels", {"destName": "广州"})
    assert len(recorder.calls) == 1


def test_mcp_client_parses_sse_and_rejects_tool_errors():
    sse_body = (
        'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":'
        '{"content":[{"type":"text","text":"{\\"status\\":0,\\"data\\":{\\"itemList\\":[]}}"}]}}\n\n'
    ).encode("utf-8")
    client = FlyAIMCPClient("key", transport=Recorder(
        TransportResponse(200, "text/event-stream", sse_body)))
    assert client.call_tool("search_hotels", {"destName": "广州"})["data"]["itemList"] == []

    failing = FlyAIMCPClient("key", transport=Recorder(reply(is_error=True)))
    with pytest.raises(FlyAIError, match="SOURCE_ERROR"):
        failing.call_tool("search_hotels", {"destName": "广州"})


def test_mcp_client_maps_auth_and_rpc_errors_without_body():
    client = FlyAIMCPClient("key", transport=Recorder(
        TransportResponse(401, "application/json", b'{"message":"no"}')))
    with pytest.raises(FlyAIError, match="AUTHENTICATION_FAILED"):
        client.call_tool("search_hotels", {"destName": "广州"})

    rpc = FlyAIMCPClient("key", transport=Recorder(TransportResponse(
        200, "application/json", b'{"jsonrpc":"2.0","id":1,"error":{"message":"x"}}')))
    with pytest.raises(FlyAIError, match="RPC_ERROR"):
        rpc.call_tool("search_hotels", {"destName": "广州"})


def test_mcp_client_signs_only_when_a_secret_is_configured():
    recorder = Recorder()
    client = FlyAIMCPClient("key", transport=recorder, sign_secret="sign-me")
    client.call_tool("search_hotels", {"destName": "广州"})
    headers = recorder.calls[0][1]
    assert headers["x-flyai-sign-ver"] == "7"
    assert headers["x-flyai-sign-alg"] == "hmac-sha256"
    assert headers["x-flyai-ts"].isdigit()
    assert len(headers["x-flyai-nonce"]) == 32
    assert headers["x-flyai-sign"]


def test_cli_client_maps_flags_and_keeps_key_out_of_argv():
    seen = {}

    def runner(command, env, timeout):
        seen["command"] = command
        seen["env"] = dict(env)
        return 3221226505, json.dumps(PAYLOAD), "assertion"

    client = FlyAICliClient("cli-secret", runner=runner)
    assert client.call_tool(
        "search_hotels", {"destName": "广州", "maxPrice": 200, "keyWords": "荔胜广场"},
    ) == PAYLOAD
    command = seen["command"]
    assert "--dest-name" in command and "广州" in command
    assert "--max-price" in command and "200" in command
    assert "--key-words" in command and "荔胜广场" in command
    assert all("cli-secret" not in part for part in command)
    assert seen["env"]["FLYAI_API_KEY"] == "cli-secret"


def test_guarded_cli_pins_the_preload_and_does_not_inherit_node_options(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--inspect")

    def runner(command, env, timeout):
        assert command[1:3] == ["--require", str(CLI_NETWORK_GUARD)]
        assert "NODE_OPTIONS" not in env
        assert env["FLYAI_API_KEY"] == "project-key"
        return 0, json.dumps(PAYLOAD), ""

    FlyAICliClient("project-key", runner=runner, guard_network=True).call_tool(
        "search_hotels", {"destName": "广州"},
    )


def test_cli_client_tolerates_windows_exit_crash_but_not_invalid_output():
    ok = FlyAICliClient("key", runner=lambda *a: (3221226505, json.dumps(PAYLOAD), ""))
    assert ok.call_tool("search_hotels", {"destName": "广州"})["status"] == 0

    dry = FlyAICliClient("key", runner=lambda *a: (
        0, json.dumps({"tool": "search_hotels", "arguments": {}}), ""))
    with pytest.raises(FlyAIError, match="INVALID_RESPONSE"):
        dry.call_tool("search_hotels", {"destName": "广州"})

    broken = FlyAICliClient("key", runner=lambda *a: (0, "not json", ""))
    with pytest.raises(FlyAIError, match="INVALID_RESPONSE"):
        broken.call_tool("search_hotels", {"destName": "广州"})


def test_cli_client_timeout_has_no_retry(monkeypatch, tmp_path):
    calls = []

    def runner(command, env, timeout):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, timeout)

    cli = tmp_path / "bundle.cjs"
    cli.write_text("// fake", encoding="utf-8")
    client = FlyAICliClient("key", cli_path=cli, runner=runner)
    with pytest.raises(FlyAIError, match="TIMEOUT"):
        client.call_tool("search_hotels", {"destName": "广州"})
    assert len(calls) == 1


def test_missing_cli_fails_closed_but_a_blank_key_is_not_fatal(tmp_path):
    # A blank project key must NOT block the channel: the official bundle
    # resolves its own credential chain. What it forbids is *claiming* the
    # project key was used, so the source is recorded instead.
    for blank in ("", "   "):
        client = FlyAICliClient(blank, runner=lambda *a: (0, '{"status": 0}', ""))
        assert client.credential_source == "cli_default_chain"
    supplied = FlyAICliClient("key", runner=lambda *a: (0, '{"status": 0}', ""))
    assert supplied.credential_source == "project_env"
    # An uninstalled bundle still fails closed.
    client = FlyAICliClient("key", cli_path=tmp_path / "missing.cjs")
    with pytest.raises(FlyAIError, match="INVALID_CONFIGURATION"):
        client.call_tool("search_hotels", {"destName": "广州"})
    with pytest.raises(FlyAIError, match="INVALID_CONFIGURATION"):
        FlyAIMCPClient("   ")


def test_blank_key_never_inherits_a_stray_parent_credential(monkeypatch):
    captured = {}

    def runner(command, env, timeout):
        captured.update(env)
        return 0, '{"status": 0}', ""

    monkeypatch.setenv("FLYAI_API_KEY", "parent-key")
    FlyAICliClient("", runner=runner).call_tool("search_hotels", {"destName": "广州"})
    assert "FLYAI_API_KEY" not in captured
    FlyAICliClient("project-key", runner=runner).call_tool(
        "search_hotels", {"destName": "广州"})
    assert captured["FLYAI_API_KEY"] == "project-key"


def test_root_points_at_the_project_root_not_the_backend_package():
    # parents[3] used to resolve to backend/, which silently hid both .env and
    # the vendored CLI. Guard the depth so it cannot regress unnoticed.
    assert (module.ROOT / "backend").is_dir()
    assert (module.ROOT / ".env.example").exists()
    assert not (module.ROOT.name == "backend")


def test_load_key_prefers_process_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FLYAI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("FLYAI_API_KEY", raising=False)
    assert load_key() == "from-file"
    monkeypatch.setenv("FLYAI_API_KEY", "from-env")
    assert load_key() == "from-env"


def test_search_callback_adapts_client_contract():
    client = FlyAIMCPClient("key", transport=Recorder())
    callback = search_callback(client)
    assert callback({"destName": "广州"}) == PAYLOAD
