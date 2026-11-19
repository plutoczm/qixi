"""Fixed-command model adapters with bounded JSONL IPC and explicit cancellation."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from stay_agent.providers.data.travel_clients import load_setting

ROOT = Path(__file__).resolve().parents[4]


class AdapterError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AdapterResult:
    provider: str
    model: str
    text: str
    usage: dict
    output_format: str


def native_schema(value):
    """OpenAI's supported subset uses anyOf; local Pydantic remains authoritative."""
    if isinstance(value, list):
        return [native_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in ("discriminator", "title"):
            continue
        result["anyOf" if key == "oneOf" else key] = native_schema(item)
    if result.get("type") == "object" and "properties" in result:
        result["additionalProperties"] = False
        result["required"] = list(result["properties"])
    return result


class NodeTransport:
    def __init__(self, command=None, timeout=60):
        self.command = command or [
            "D:/nodejs/node.exe",
            str(ROOT / "tools/model-bridge/bridge.mjs"),
        ]
        self.timeout = timeout

    def generate(
        self,
        route,
        system,
        prompt,
        schema,
        cancelled,
        on_started=None,
        deadline=None,
        on_progress=None,
    ):
        if route.provider not in ("deepseek", "pi_chatgpt"):
            raise AdapterError("UNKNOWN_PROVIDER")
        deadline = min(deadline or float("inf"), time.monotonic() + self.timeout)
        if deadline <= time.monotonic():
            raise AdapterError("TIMEOUT")
        if cancelled.is_set():
            raise AdapterError("CANCELLED")
        request = {
            "type": "generate",
            "provider": route.provider,
            "model": route.model,
            "system": system,
            "prompt": prompt,
            "schema": native_schema(schema),
            "max_tokens": 2048,
        }
        secret = None
        if route.provider == "deepseek":
            secret = load_setting("DEEPSEEK_API_KEY")
            request["api_key"] = secret
        payload = (json.dumps(request, ensure_ascii=False) + "\n").encode()
        if len(payload) > 65536:
            raise AdapterError("REQUEST_LIMIT")
        work = ROOT / "runtime/model-worker"
        work.mkdir(parents=True, exist_ok=True)
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "PATH",
                "USERPROFILE",
                "HOMEDRIVE",
                "HOMEPATH",
            }
        }
        process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=work,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        events = queue.Queue(maxsize=16)
        reader_stopped = threading.Event()

        def read_events():
            try:
                while not reader_stopped.is_set():
                    line = process.stdout.readline(262145)
                    if not line:
                        item = ("eof", None)
                    elif len(line) > 262144 or not line.endswith(b"\n"):
                        item = ("error", "INVALID_BRIDGE_RESPONSE")
                    else:
                        try:
                            item = ("event", json.loads(line.decode("utf-8")))
                        except (ValueError, UnicodeError):
                            item = ("error", "INVALID_BRIDGE_RESPONSE")
                    try:
                        events.put(item, timeout=0.2)
                    except queue.Full:
                        break
                    if item[0] != "event":
                        break
            finally:
                reader_stopped.set()

        reader = threading.Thread(target=read_events, daemon=True)
        reader.start()
        try:
            process.stdin.write(payload)
            process.stdin.flush()
            while True:
                if cancelled.is_set():
                    raise AdapterError("CANCELLED")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AdapterError("TIMEOUT")
                try:
                    kind, data = events.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    if reader_stopped.is_set() and events.empty():
                        raise AdapterError("BRIDGE_UNAVAILABLE")
                    continue
                if kind != "event" or not isinstance(data, dict):
                    raise AdapterError(
                        "BRIDGE_UNAVAILABLE" if kind == "eof" else "INVALID_BRIDGE_RESPONSE"
                    )
                event_type = data.get("type")
                if event_type == "started":
                    if on_started:
                        on_started()
                elif event_type == "progress":
                    count = data.get("output_bytes")
                    if type(count) is not int or not 0 < count <= 65536:
                        raise AdapterError("INVALID_BRIDGE_RESPONSE")
                    if on_progress:
                        on_progress(count)
                elif event_type == "failed":
                    allowed = {
                        "TIMEOUT",
                        "CANCELLED",
                        "UNKNOWN_MODEL",
                        "INVALID_CONFIGURATION",
                        "ENDPOINT_DENIED",
                        "OUTPUT_LIMIT",
                        "OUTPUT_TRUNCATED",
                        "UNEXPECTED_TOOL_CALL",
                        "AUTHENTICATION_FAILED",
                        "ACCESS_DENIED",
                        "RATE_LIMITED",
                        "INSUFFICIENT_BALANCE",
                        "PROVIDER_UNAVAILABLE",
                        "PROVIDER_ERROR",
                        "SECRET_EXPOSURE_BLOCKED",
                    }
                    raise AdapterError(
                        data.get("code") if data.get("code") in allowed else "PROVIDER_ERROR"
                    )
                elif event_type == "completed":
                    if data.get("provider") != route.provider or data.get("model") != route.model:
                        raise AdapterError("ROUTE_MISMATCH")
                    text = data.get("text")
                    if not isinstance(text, str) or len(text.encode()) > 32768:
                        raise AdapterError("INVALID_BRIDGE_RESPONSE")
                    if secret and secret in text:
                        raise AdapterError("SECRET_EXPOSURE_BLOCKED")
                    usage = data.get("usage", {})
                    if not isinstance(usage, dict) or any(
                        type(n) is not int or not 0 <= n <= 2000000 for n in usage.values()
                    ):
                        raise AdapterError("INVALID_BRIDGE_RESPONSE")
                    if cancelled.is_set():
                        raise AdapterError("CANCELLED")
                    return AdapterResult(
                        route.provider,
                        route.model,
                        text,
                        usage,
                        data.get("format", "validated_json"),
                    )
                else:
                    raise AdapterError("INVALID_BRIDGE_RESPONSE")
        finally:
            reader_stopped.set()
            if process.poll() is None:
                try:
                    process.stdin.write(b'{"type":"cancel"}\n')
                    process.stdin.flush()
                except (OSError, ValueError):
                    pass
                try:
                    process.wait(timeout=0.8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
            for pipe in (process.stdin, process.stdout):
                try:
                    pipe.close()
                except OSError:
                    pass
            reader.join(timeout=1)


class ModelRouter:
    def __init__(self, transport=None):
        self.transport = transport or NodeTransport()

    def resolve(self, provider="deepseek"):
        from stay_agent.generation import ModelRoute

        if provider == "deepseek":
            return ModelRoute("deepseek", "deepseek-flash")
        if provider == "pi_chatgpt":
            raise AdapterError("PROVIDER_DISABLED")
        raise AdapterError("UNKNOWN_PROVIDER")

    def generate(self, route, *args, **kwargs):
        # Never switch providers on missing auth, bad output, timeout or usage limits.
        if route.provider != "deepseek":
            raise AdapterError("PROVIDER_DISABLED")
        return self.transport.generate(route, *args, **kwargs)
