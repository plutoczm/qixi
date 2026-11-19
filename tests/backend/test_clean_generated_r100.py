"""R100: regenerable project cleanup must not touch business state or evidence."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "clean_generated.py"


def load_cleaner():
    spec = importlib.util.spec_from_file_location("r100_clean_generated", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sentinel", encoding="utf-8")
    return path


def test_dry_run_finds_only_regenerable_artifacts(tmp_path: Path):
    cleaner = load_cleaner()
    cache_a = create(tmp_path, ".coverage")
    cache_b = create(tmp_path, ".pytest_cache/v/cache/nodeids")
    cache_c = create(tmp_path, "backend/stay_agent/__pycache__/main.pyc")
    cache_d = create(tmp_path, "tests/backend/__pycache__/test_old.pyc")
    cache_e = create(tmp_path, "scripts/__pycache__/dev.pyc")
    cache_f = create(tmp_path, "frontend/test-results/.last-run.json")
    cache_g = create(tmp_path, "frontend/tsconfig.tsbuildinfo")
    cache_h = create(tmp_path, "frontend/node_modules/.vite-temp/file.json")
    protected = [
        create(tmp_path, ".env"),
        create(tmp_path, "runtime/search.sqlite3"),
        create(tmp_path, "runtime/personal-backups/keep.sqlite3"),
        create(tmp_path, "runtime/personal-restores/keep.sqlite3"),
        create(tmp_path, "runtime/r99-browser-summary.json"),
        create(tmp_path, "frontend/dist/index.html"),
        create(tmp_path, "frontend/node_modules/react/index.js"),
        create(tmp_path, "dist/windows/Xunqi.exe"),
        create(tmp_path, "docs/evidence/immutable.jsonl"),
        create(tmp_path, "frontend/src/App.tsx"),
    ]
    expected = {
        ".coverage", ".pytest_cache", "backend/stay_agent/__pycache__",
        "tests/backend/__pycache__", "scripts/__pycache__", "frontend/test-results",
        "frontend/tsconfig.tsbuildinfo", "frontend/node_modules/.vite-temp",
    }
    targets = cleaner.collect_targets(tmp_path)
    assert {p.relative_to(tmp_path).as_posix() for p in targets} == expected
    assert cleaner.clean_generated(tmp_path) == targets
    for path in (cache_a, cache_b, cache_c, cache_d, cache_e, cache_f, cache_g, cache_h, *protected):
        assert path.exists(), f"Dry-run removed {path}"


def test_apply_removes_only_allowlisted_cache_targets(tmp_path: Path):
    cleaner = load_cleaner()
    cache = create(tmp_path, "backend/stay_agent/providers/__pycache__/thing.pyc")
    cache2 = create(tmp_path, ".ruff_cache/something")
    cache3 = create(tmp_path, "frontend/node_modules/.vitest-cache/thing")
    user_state = create(tmp_path, "runtime/conversations.sqlite3")
    private = create(tmp_path, ".env")
    exe = create(tmp_path, "dist/windows/Xunqi.exe")
    source = create(tmp_path, "frontend/src/main.tsx")
    cleaner.clean_generated(tmp_path, apply=True)
    assert not cache.exists()
    assert not cache2.exists()
    assert not cache3.exists()
    for p in [user_state, private, exe, source]:
        assert p.read_text(encoding="utf-8") == "sentinel"


def test_does_not_follow_symlink_outside_the_project(tmp_path: Path):
    cleaner = load_cleaner()
    outside = tmp_path.parent / f"{tmp_path.name}-do-not-delete"
    secret = create(outside, "sensitive.txt")
    shortcut = tmp_path / ".pytest_cache"
    try:
        shortcut.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Windows symbolic links unavailable")
    assert shortcut not in cleaner.collect_targets(tmp_path)
    cleaner.clean_generated(tmp_path, apply=True)
    assert secret.exists()

def test_never_deletes_a_git_tracked_file_even_inside_a_cache_named_directory(tmp_path: Path):
    cleaner = load_cleaner()
    create(tmp_path, ".pytest_cache/pinned.txt")
    create(tmp_path, ".coverage")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", ".pytest_cache/pinned.txt"], check=True)
    targets = cleaner.collect_targets(tmp_path)
    assert {p.relative_to(tmp_path).as_posix() for p in targets} == {".coverage"}
    cleaner.clean_generated(tmp_path, apply=True)
    assert (tmp_path / ".pytest_cache/pinned.txt").read_text(encoding="utf-8") == "sentinel"
    assert not (tmp_path / ".coverage").exists()
