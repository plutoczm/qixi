"""Remove only regenerable development artifacts, never local app data.

Default is a read-only preview. Pass --apply to delete this script's fixed
allowlist; no arbitrary paths, no external providers, no files hashes.
The desktop EXE, frontend build, node_modules dependencies, .env, runtime,
SQLite databases, backups, evidence and archived experiments are excluded.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# Fixed allowlist, independent of command-line arguments.
EXACT_CACHE_PATHS = (
    ".coverage",
    ".pytest_cache",
    ".ruff_cache",
    "frontend/test-results",
    "frontend/tsconfig.tsbuildinfo",
    "frontend/node_modules/.vite",
    "frontend/node_modules/.vite-temp",
    "frontend/node_modules/.vitest",
    "frontend/node_modules/.vitest-cache",
)
PYCACHE_ROOTS = ("backend", "scripts", "tests")


def _is_link_or_junction(path: Path) -> bool:
    junction_check = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(junction_check and junction_check())


def _safe_target(root: Path, candidate: Path) -> bool:
    """Reject symlinks/junctions, including a linked ancestor."""
    if not candidate.is_relative_to(root) or candidate == root:
        return False
    part = candidate
    while part != root:
        if _is_link_or_junction(part):
            return False
        part = part.parent
    return candidate.resolve().is_relative_to(root)


def collect_targets(root: Path = ROOT) -> list[Path]:
    """Return the exact existing, safely regenerable targets in this checkout."""
    root = root.resolve(strict=True)
    targets: set[Path] = set()
    for relative in EXACT_CACHE_PATHS:
        candidate = root / relative
        if candidate.exists() and _safe_target(root, candidate):
            targets.add(candidate)

    for relative in PYCACHE_ROOTS:
        base = root / relative
        if not base.is_dir() or not _safe_target(root, base):
            continue
        for dirname, subdirs, _files in os.walk(base, topdown=True, followlinks=False):
            current = Path(dirname)
            # Do not descend through symlinks, Windows junctions or nested caches.
            subdirs[:] = [
                child for child in subdirs
                if _safe_target(root, current / child)
            ]
            for name in tuple(subdirs):
                if name == "__pycache__":
                    targets.add(current / name)
                    subdirs.remove(name)
    # Never delete a Git-tracked file, even if force-added to an ignored
    # directory. Git failure is fatal rather than permission to delete.
    tracked: set[str] = set()
    if (root / ".git").exists():
        raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
        tracked = {name.decode("utf-8") for name in raw.split(b"\\0") if name}
    eligible = []
    for candidate in targets:
        relative = candidate.relative_to(root).as_posix()
        if relative in tracked or any(name.startswith(relative + "/") for name in tracked):
            continue
        eligible.append(candidate)
    return sorted(eligible, key=lambda path: path.relative_to(root).as_posix())


def clean_generated(root: Path = ROOT, *, apply: bool = False) -> list[Path]:
    """Preview targets by default, or delete only the discovered allowlist."""
    targets = collect_targets(root)
    if apply:
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Actually remove the fixed cache allowlist."
    )
    args = parser.parse_args()
    candidates = clean_generated(ROOT, apply=args.apply)
    state = "removed" if args.apply else "dry-run; would remove"
    print(f"{state}: {len(candidates)} regenerable file/directory targets")
    for target in candidates:
        print(f"  {target.relative_to(ROOT).as_posix()}")
    print("Protected: .env, runtime/, dist/, frontend/dist/, installed dependencies, "
          "source code, archives, and evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
