"""Offline 1.0 resume points: validate two SQLite stores and record the exact committed state."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

from .offline_dataset import DATASET_ID, DATASET_VERSION

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SEARCH_SCHEMA = {
    "search_sessions": ("request_id", "created_at", "expires_at", "context_json", "hotel_ids_json"),
    "hotel_contexts": ("request_id", "hotel_id", "name", "address"),
}
CONVERSATION_SCHEMA = {
    "conversations": ("id", "revision", "generation", "client_sequence", "draft", "expires"),
    "operations": (
        "id", "conversation_id", "sequence", "generation", "base_revision",
        "fingerprint", "provider", "model", "status", "error_code",
    ),
}


class CheckpointError(Exception):
    """A resume point is incomplete, inconsistent or not compatible with this app."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(root: Path) -> str:
    value = os.environ.get("XUNQI_APP_COMMIT", "").strip()
    if value:
        return value
    for marker in (root / "APP_COMMIT", root / "docker/APP_COMMIT"):
        if not marker.is_file():
            continue
        value = marker.read_text(encoding="utf-8").strip()
        if value:
            return value
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise CheckpointError("APP_COMMIT_UNAVAILABLE") from error


def _inspect_database(path: Path, expected_version: int, tables: dict[str, tuple[str, ...]]) -> dict:
    if not path.is_file():
        raise CheckpointError("DATABASE_MISSING")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if integrity != "ok":
                raise CheckpointError("INTEGRITY_FAILED")
            if version != expected_version:
                raise CheckpointError("UNSUPPORTED_DATABASE_VERSION")
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if set(tables) - names:
                raise CheckpointError("TABLE_MISSING")
            fk_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            if fk_errors:
                raise CheckpointError("FOREIGN_KEY_MISMATCH")
            found = {}
            for table, columns in tables.items():
                actual = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
                if actual != columns:
                    raise CheckpointError("TABLE_SCHEMA_MISMATCH")
                found[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            return {"user_version": version, "tables": found}
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise CheckpointError("DATABASE_UNREADABLE") from error


def _consistent_backup(source: Path, destination: Path) -> None:
    if destination.exists():
        raise CheckpointError("CHECKPOINT_ALREADY_EXISTS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
            writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            writer.close()
            reader.close()
    except sqlite3.Error as error:
        raise CheckpointError("BACKUP_FAILED") from error
    for suffix in ("-wal", "-shm"):
        sidecar = destination.with_name(destination.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _state_paths(root: Path, runtime_root: Path | None = None) -> tuple[Path, Path]:
    base = Path(runtime_root) if runtime_root is not None else root / "runtime"
    return base / "offline_search.sqlite3", base / "offline_conversations.sqlite3"


def _resume_points(search_path: Path, conversation_path: Path) -> dict:
    search = _inspect_database(search_path, 2, SEARCH_SCHEMA)
    conversation = _inspect_database(conversation_path, 1, CONVERSATION_SCHEMA)
    search_db = sqlite3.connect(f"file:{search_path}?mode=ro", uri=True)
    conversation_db = sqlite3.connect(f"file:{conversation_path}?mode=ro", uri=True)
    try:
        hotel_metadata: dict[str, list[dict]] = {}
        for row in search_db.execute(
            "SELECT request_id,hotel_id,name,address FROM hotel_contexts ORDER BY request_id,hotel_id"
        ):
            hotel_metadata.setdefault(row[0], []).append(
                {"id": row[1], "name": row[2], "address": row[3]}
            )
        sessions = [
            {
                "request_id": row[0],
                "created_at": row[1],
                "expires_at": row[2],
                "context": json.loads(row[3]),
                "hotel_ids": json.loads(row[4]),
                "hotel_metadata": hotel_metadata.get(row[0], []),
            }
            for row in search_db.execute(
                "SELECT request_id,created_at,expires_at,context_json,hotel_ids_json "
                "FROM search_sessions ORDER BY created_at,request_id"
            )
        ]
        conversations = [
            {
                "conversation_id": row[0],
                "revision": row[1],
                "generation": row[2],
                "client_sequence": row[3],
                "draft": json.loads(row[4]),
                "expires_at": row[5],
            }
            for row in conversation_db.execute(
                "SELECT id,revision,generation,client_sequence,draft,expires "
                "FROM conversations ORDER BY id"
            )
        ]
    except (json.JSONDecodeError, sqlite3.Error) as error:
        raise CheckpointError("STATE_UNREADABLE") from error
    finally:
        search_db.close()
        conversation_db.close()
    return {
        "search_sessions": sessions,
        "conversations": conversations,
        "search_schema": search,
        "conversation_schema": conversation,
    }


def create_checkpoint(
    root: Path, label: str, *, reason: str = "connection_lost", runtime_root: Path | None = None
) -> Path:
    """Save a disconnection point before an offline app stops or is upgraded."""
    if not SAFE_ID.fullmatch(label):
        raise CheckpointError("INVALID_CHECKPOINT_ID")
    root = Path(root).resolve()
    state_root = Path(runtime_root).resolve() if runtime_root is not None else root / "runtime"
    destination = state_root / "checkpoints" / label
    if destination.exists():
        raise CheckpointError("CHECKPOINT_ALREADY_EXISTS")
    destination.mkdir(parents=True)
    search_path, conversation_path = _state_paths(root, runtime_root)
    state = _resume_points(search_path, conversation_path)
    files: dict[str, dict] = {}
    try:
        for key, source in (("search", search_path), ("conversation", conversation_path)):
            name = f"{key}.sqlite3"
            _consistent_backup(source, destination / name)
            files[name] = {
                "relative_path": source.relative_to(root).as_posix()
                if source.is_relative_to(root) else source.name,
                "sha256": _sha256(destination / name),
                "size": (destination / name).stat().st_size,
            }
        saved_at = _now().isoformat()
        record = {
            "schema": "xunqi-offline-checkpoint-v1",
            "checkpoint_id": label,
            "reason": reason,
            "saved_at": saved_at,
            "disconnected_at": saved_at,
            "app_commit": _git_commit(root),
            "dataset": {"id": DATASET_ID, "version": DATASET_VERSION},
            "data_mode": "offline",
            "files": files,
            "database": {
                "search": state["search_schema"],
                "conversation": state["conversation_schema"],
            },
            "resume": state,
            "notes": [
                "只保存已提交的两个SQLite状态；进程内点位／队列／游标不跨重启恢复。",
                "恢复时校验应用提交、数据集版本、完整性、schema和文件SHA256；错误schema拒绝恢复并保留现场。",
            ],
        }
        (destination / "checkpoint.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def _validate_record(directory: Path, root: Path, *, allow_commit_mismatch: bool) -> dict:
    if not directory.is_dir():
        raise CheckpointError("CHECKPOINT_NOT_FOUND")
    try:
        record = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckpointError("CHECKPOINT_RECORD_UNREADABLE") from error
    if record.get("schema") != "xunqi-offline-checkpoint-v1":
        raise CheckpointError("UNSUPPORTED_CHECKPOINT_SCHEMA")
    if record.get("data_mode") != "offline":
        raise CheckpointError("DATA_MODE_MISMATCH")
    dataset = record.get("dataset") or {}
    if dataset.get("id") != DATASET_ID or dataset.get("version") != DATASET_VERSION:
        raise CheckpointError("DATASET_VERSION_MISMATCH")
    if not allow_commit_mismatch and record.get("app_commit") != _git_commit(root):
        raise CheckpointError("APP_COMMIT_MISMATCH")
    for name, expected in (record.get("files") or {}).items():
        path = directory / name
        if not path.is_file() or _sha256(path) != expected.get("sha256"):
            raise CheckpointError("CHECKPOINT_FILE_MISMATCH")
    return record


def inspect_checkpoint(
    root: Path, label: str, *, allow_commit_mismatch: bool = False,
    runtime_root: Path | None = None,
) -> dict:
    if not SAFE_ID.fullmatch(label):
        raise CheckpointError("INVALID_CHECKPOINT_ID")
    root = Path(root).resolve()
    state_root = Path(runtime_root).resolve() if runtime_root is not None else root / "runtime"
    directory = state_root / "checkpoints" / label
    record = _validate_record(directory, root, allow_commit_mismatch=allow_commit_mismatch)
    _inspect_database(directory / "search.sqlite3", 2, SEARCH_SCHEMA)
    _inspect_database(directory / "conversation.sqlite3", 1, CONVERSATION_SCHEMA)
    return record


def restore_checkpoint(
    root: Path,
    label: str,
    *,
    allow_commit_mismatch: bool = False,
    expected_git_commit: str | None = None,
    runtime_root: Path | None = None,
) -> Path:
    """Restore both validated stores into an isolated runtime directory, never the live runtime."""
    if not SAFE_ID.fullmatch(label):
        raise CheckpointError("INVALID_CHECKPOINT_ID")
    root = Path(root).resolve()
    state_root = Path(runtime_root).resolve() if runtime_root is not None else root / "runtime"
    directory = state_root / "checkpoints" / label
    record = _validate_record(directory, root, allow_commit_mismatch=allow_commit_mismatch)
    if expected_git_commit is not None and record.get("app_commit") != expected_git_commit:
        raise CheckpointError("APP_COMMIT_MISMATCH")
    _inspect_database(directory / "search.sqlite3", 2, SEARCH_SCHEMA)
    _inspect_database(directory / "conversation.sqlite3", 1, CONVERSATION_SCHEMA)
    target = state_root / f"restored-{label}"
    if target.exists():
        raise CheckpointError("RESTORE_TARGET_EXISTS")
    state = inspect_checkpoint(root, label, allow_commit_mismatch=allow_commit_mismatch,
                               runtime_root=runtime_root)
    try:
        target.mkdir(parents=True)
        shutil.copyfile(directory / "search.sqlite3", target / "offline_search.sqlite3")
        shutil.copyfile(directory / "conversation.sqlite3", target / "offline_conversations.sqlite3")
        _inspect_database(target / "offline_search.sqlite3", 2, SEARCH_SCHEMA)
        _inspect_database(target / "offline_conversations.sqlite3", 1, CONVERSATION_SCHEMA)
        nested = target / "checkpoints" / label
        nested.mkdir(parents=True)
        for name in ("checkpoint.json", "search.sqlite3", "conversation.sqlite3"):
            shutil.copyfile(directory / name, nested / name)
        (target / "RESTORED.json").write_text(
            json.dumps(
                {
                    "checkpoint_id": label,
                    "disconnected_at": record.get("disconnected_at"),
                    "restored_at": _now().isoformat(),
                    "app_commit": record.get("app_commit"),
                    "search_sessions": state["resume"]["search_sessions"],
                    "conversations": state["resume"]["conversations"],
                    "limits": "地点、路线和续查游标为进程内状态，不能从断点恢复；重新查询即可继续。",
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def health_payload(connected: bool, record: dict | None = None) -> dict:
    """Small, UI-safe status; a live service is not a broken connection."""
    payload = {
        "connected": connected,
        "status": "connected" if connected else "disconnected",
        "disconnected_at": None,
    }
    if record:
        sessions = record.get("resume", {}).get("search_sessions") or []
        conversations = record.get("resume", {}).get("conversations") or []
        payload.update(
            {
                "checkpoint_id": record.get("checkpoint_id"),
                "app_commit": record.get("app_commit"),
                "saved_at": record.get("saved_at") or record.get("disconnected_at"),
                "disconnected_at": record.get("disconnected_at"),
                "search_session_count": len(sessions),
                "conversation_count": len(conversations),
                "resume_available": bool(sessions or conversations),
            }
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "inspect", "restore"))
    parser.add_argument("label")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--reason", default="connection_lost")
    parser.add_argument("--allow-commit-mismatch", action="store_true")
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--runtime-root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    state_root = Path(args.runtime_root).resolve() if args.runtime_root else None
    try:
        if args.command == "create":
            path = create_checkpoint(root, args.label, reason=args.reason,
                                     runtime_root=state_root)
            print(f"CHECKPOINT_CREATED {path}")
        elif args.command == "inspect":
            record = inspect_checkpoint(root, args.label,
                                        allow_commit_mismatch=args.allow_commit_mismatch,
                                        runtime_root=state_root)
            print(json.dumps(record, ensure_ascii=False, indent=2))
        else:
            path = restore_checkpoint(
                root, args.label,
                allow_commit_mismatch=args.allow_commit_mismatch,
                expected_git_commit=args.expected_git_commit,
                runtime_root=state_root,
            )
            print(f"CHECKPOINT_RESTORED {path}")
        return 0
    except CheckpointError as error:
        print(f"CHECKPOINT_FAILED {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
