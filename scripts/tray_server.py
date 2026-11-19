"""On-demand loopback server controlled by Xunqi's Windows tray launcher.

One Python process, no autostart, no provider requests on startup.
The launcher signals this process to stop through a private, unique marker
inside runtime/launcher. Uvicorn then shuts down its lifespan gracefully.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_RUNTIME = ROOT / "runtime" / "launcher"


def validate_marker(raw: str) -> Path:
    marker = Path(raw).resolve()
    if marker.parent != LAUNCHER_RUNTIME.resolve() or not (
        marker.name.startswith("stop-") and marker.suffix == ".flag"
    ):
        raise ValueError("INVALID_LAUNCHER_EXIT_MARKER")
    return marker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--exit-marker", required=True)
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 12) or Path(sys.prefix).name.lower() != "xunqi":
        print("Use the project's Python 3.12 Conda xunqi environment.", flush=True)
        return 2
    if not 1024 <= args.port <= 65535:
        print("INVALID_PORT", flush=True)
        return 2
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        print("FRONTEND_BUILD_MISSING", flush=True)
        return 2
    try:
        marker = validate_marker(args.exit_marker)
    except ValueError as exc:
        print(str(exc), flush=True)
        return 2
    if marker.exists():
        print("STALE_STOP_MARKER_REFUSED", flush=True)
        return 2

    # This is the same personal live source as scripts/dev.py. A model/provider
    # request can happen only after a user explicitly sends it from the UI.
    os.environ["XUNQI_DATA_MODE"] = "live"
    os.environ["XUNQI_HOTEL_SOURCE"] = "rollinggo"
    # uvicorn.Config does not accept CLI-only app_dir; add the local source
    # directory to sys.path before asking Uvicorn to import the app.
    sys.path.insert(0, str(ROOT / "backend"))
    import uvicorn

    cfg = uvicorn.Config(
        "stay_agent.main:app",
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="info",
    )
    server = uvicorn.Server(cfg)
    stopped = threading.Event()

    def watch_exit_marker() -> None:
        while not stopped.wait(0.4):
            if marker.is_file():
                server.should_exit = True
                return

    watcher = threading.Thread(target=watch_exit_marker, name="tray-exit", daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        stopped.set()
        watcher.join(timeout=1.0)
        if marker.exists():
            marker.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
