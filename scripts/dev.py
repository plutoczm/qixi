"""Start the built local website with the project Conda interpreter."""

from pathlib import Path
import argparse
import importlib.util
import os
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--runtime-dir",
        help="Use an isolated project-local runtime directory (for a restored checkpoint).",
    )
    parser.add_argument(
        "--build", action="store_true", help="Build installed frontend dependencies before serving."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check prerequisites without launching or calling providers.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the 1.0 offline synthetic demo instead of the 2.0 real project.",
    )
    parser.add_argument(
        "--hotel-source",
        choices=("rollinggo", "tuniu"),
        help="Explicit quote source for this run; never falls back automatically.",
    )
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or Path(sys.prefix).name.lower() != "xunqi":
        print("Activate the xunqi Conda environment (Python 3.12) before running this script.")
        return 2
    missing = [
        name
        for name in ("fastapi", "uvicorn", "pydantic", "tzdata")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        print("Missing project dependencies: " + ", ".join(missing))
        print("Install the approved requirements in the xunqi environment first.")
        return 2
    if not 1024 <= args.port <= 65535:
        print("Use a local port between 1024 and 65535.")
        return 2
    if args.build:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if npm is None:
            print("npm is not available on PATH. Activate the project environment and install Node.js.")
            return 2
        if not (ROOT / "frontend/node_modules").is_dir():
            print("Frontend dependencies are not installed.")
            return 2
        result = subprocess.run(
            [npm, "--prefix", str(ROOT / "frontend"), "run", "build"], cwd=ROOT
        )
        if result.returncode:
            return result.returncode
    if not (ROOT / "frontend/dist/index.html").is_file():
        print("Frontend build missing. Run the approved npm build or pass --build.")
        return 2
    if args.runtime_dir:
        runtime_root = (ROOT / "runtime").resolve()
        candidate = (ROOT / args.runtime_dir).resolve()
        if not candidate.is_relative_to(runtime_root):
            print("--runtime-dir must stay inside this project's runtime directory.")
            return 2
        os.environ["XUNQI_RUNTIME_DIR"] = str(candidate)
    if args.check:
        print("Project environment and built website are ready. No provider call was made.")
        print("2.0 data mode: live real providers (--demo runs the 1.0 synthetic dataset).")
        return 0
    try:
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", args.port))
    except OSError:
        print("This local port is already in use. Choose another --port; no process was stopped.")
        return 2
    print(f"Xunqi: http://127.0.0.1:{args.port}", flush=True)
    if args.demo:
        os.environ["XUNQI_DATA_MODE"] = "offline"
    elif os.environ.get("XUNQI_DATA_MODE") not in ("offline", "live"):
        os.environ["XUNQI_DATA_MODE"] = "live"
    if args.hotel_source:
        os.environ["XUNQI_HOTEL_SOURCE"] = args.hotel_source
    elif "XUNQI_HOTEL_SOURCE" not in os.environ:
        os.environ["XUNQI_HOTEL_SOURCE"] = "rollinggo"
    print(
        f"data mode: {os.environ['XUNQI_DATA_MODE']} | quote source: "
        f"{os.environ['XUNQI_HOTEL_SOURCE']} (no provider call on startup)",
        flush=True,
    )
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "stay_agent.main:app",
            "--app-dir",
            "backend",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--no-access-log",
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
