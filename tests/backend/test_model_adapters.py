"""Real subprocess IPC tests with synthetic helpers; no network or live credentials."""

import json
import subprocess
import sys
import threading
import time

import pytest

from stay_agent.generation import ModelRoute
from stay_agent.providers.llm.adapters import AdapterError, ModelRouter, NodeTransport, native_schema
from stay_agent.model_output import ConditionProposal


@pytest.fixture(autouse=True)
def synthetic_secret(monkeypatch):
    monkeypatch.setattr("stay_agent.providers.llm.adapters.load_setting", lambda *a, **k: "synthetic-key")


def helper(tmp_path, body, timeout=2):
    path = tmp_path / "helper.py"
    path.write_text(
        "import json,sys,time,os\nrequest=json.loads(sys.stdin.readline())\n" + body,
        encoding="utf-8",
    )
    return NodeTransport([sys.executable, "-X", "utf8", "-u", str(path)], timeout=timeout)


def call(transport, cancelled=None, started=None):
    return transport.generate(
        ModelRoute("deepseek", "deepseek-flash"),
        "json only",
        "synthetic prompt",
        ConditionProposal.model_json_schema(),
        cancelled or threading.Event(),
        on_started=started,
    )


def done(**changes):
    response = {
        "type": "completed",
        "provider": "deepseek",
        "model": "deepseek-flash",
        "text": '{"changes":[],"notes":[]}',
        "usage": {"input": 1, "output": 1},
        "format": "json_object",
        **changes,
    }
    return "print(" + repr(json.dumps(response, ensure_ascii=False)) + ",flush=True)\n"


def test_completed_response_is_bound_to_provider_and_model(tmp_path):
    result = call(helper(tmp_path, done()))
    assert result.provider == "deepseek" and result.usage["output"] == 1
    assert result.output_format == "json_object"


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "pi_chatgpt"},
        {"model": "unexpected-model"},
        {"usage": {"input": True}},
        {"usage": {"input": -1}},
        {"text": "synthetic-key"},
        {"text": None},
    ],
)
def test_mismatch_bad_metadata_or_secret_never_becomes_success(tmp_path, changes):
    with pytest.raises(AdapterError):
        call(helper(tmp_path, done(**changes)))


def test_cancel_before_start_never_spawns_or_resolves_a_key(monkeypatch):
    cancel = threading.Event()
    cancel.set()

    def forbidden(*args, **kwargs):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(AdapterError, match="CANCELLED"):
        call(NodeTransport(), cancel)


def test_cancel_midstream_discards_partial_text_and_ends_owned_process(tmp_path, monkeypatch):
    processes = []
    popen = subprocess.Popen

    def record(*a, **k):
        process = popen(*a, **k)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record)
    cancel = threading.Event()
    transport = helper(
        tmp_path,
        "print(json.dumps({'type':'started'}),flush=True)\nsys.stdin.readline()\n"
        + done(text="late result"),
    )
    with pytest.raises(AdapterError, match="CANCELLED"):
        call(transport, cancel, lambda: cancel.set())
    assert len(processes) == 1 and processes[0].poll() is not None


def test_cancel_ignoring_helper_is_terminated_not_left_running(tmp_path, monkeypatch):
    processes = []
    popen = subprocess.Popen

    def record(*a, **k):
        process = popen(*a, **k)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record)
    cancel = threading.Event()
    started = time.monotonic()
    transport = helper(
        tmp_path, "print(json.dumps({'type':'started'}),flush=True)\ntime.sleep(30)\n"
    )
    with pytest.raises(AdapterError, match="CANCELLED"):
        call(transport, cancel, lambda: cancel.set())
    assert time.monotonic() - started < 4 and processes[0].poll() is not None


def test_timeout_is_bounded_and_cannot_surface_late_completion(tmp_path):
    transport = helper(tmp_path, "time.sleep(30)\n" + done(), timeout=0.1)
    with pytest.raises(AdapterError, match="TIMEOUT"):
        call(transport)


def test_unicode_line_separator_inside_json_is_not_an_ipc_frame(tmp_path):
    result = call(helper(tmp_path, done(text="synthetic\u2028value")))
    assert result.text == "synthetic\u2028value"


def test_unknown_error_body_does_not_leak_supplier_text(tmp_path):
    transport = helper(
        tmp_path,
        "print(json.dumps({'type':'failed','code':'synthetic-key private error'}),flush=True)\n",
    )
    with pytest.raises(AdapterError) as caught:
        call(transport)
    assert str(caught.value) == "PROVIDER_ERROR"


def test_native_schema_transforms_unsupported_discriminator_without_weakening_local_schema():
    local = ConditionProposal.model_json_schema()
    formatted = native_schema(local)
    wire = json.dumps(formatted)
    assert '"oneOf"' not in wire and '"discriminator"' not in wire
    assert "discriminator" in json.dumps(local)
    assert formatted["additionalProperties"] is False


def test_router_never_retries_or_falls_back_to_another_provider():
    class Fail:
        calls = []

        def generate(self, route, *args, **kwargs):
            self.calls.append(route)
            raise AdapterError("RATE_LIMITED")

    transport = Fail()
    router = ModelRouter(transport)
    route = router.resolve()
    with pytest.raises(AdapterError, match="RATE_LIMITED"):
        router.generate(route, "system", "prompt", {}, threading.Event())
    assert len(transport.calls) == 1 and transport.calls[0].provider == "deepseek"
    with pytest.raises(AdapterError, match="UNKNOWN_PROVIDER"):
        router.resolve("other")


def test_deferred_gpt_resolve_and_generate_never_reach_a_transport():
    class Forbidden:
        def generate(self, *args, **kwargs):
            raise AssertionError("GPT was deferred")

    router = ModelRouter(Forbidden())
    with pytest.raises(AdapterError, match="PROVIDER_DISABLED"):
        router.resolve("pi_chatgpt")
    with pytest.raises(AdapterError, match="PROVIDER_DISABLED"):
        router.generate(ModelRoute("pi_chatgpt", "gpt-6-luna"))


def test_expired_deadline_does_not_start_a_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("past deadline must not spawn")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(AdapterError, match="TIMEOUT"):
        NodeTransport().generate(
            ModelRoute("deepseek", "deepseek-flash"),
            "json",
            "test",
            {},
            threading.Event(),
            deadline=time.monotonic() - 1,
        )


@pytest.mark.parametrize("code", ["INSUFFICIENT_BALANCE", "PROVIDER_UNAVAILABLE", "RATE_LIMITED"])
def test_bridge_safe_failure_categories_pass_through_without_model_completion(tmp_path, code):
    transport = helper(
        tmp_path,
        "print(json.dumps({'type':'failed','code':" + repr(code) + "}),flush=True)\n",
    )
    with pytest.raises(AdapterError, match=code):
        call(transport)
