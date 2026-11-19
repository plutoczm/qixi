"""R88: local, non-destructive backup for personal live state and quota history.

Only SQLite online backup / integrity / schema / row-count checks are used.
No API requests, SHA256 checks, credentials, or in-place live restoration.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FORMAT = "xunqi-personal-backup-v1"

# All three are important: losing the quota ledger can reset budget enforcement.
SCHEMAS: dict[str, tuple[int, dict[str, tuple[str, ...]]]] = {
    "search.sqlite3": (
        2,
        {
            "search_sessions": (
                "request_id", "created_at", "expires_at", "context_json", "hotel_ids_json",
            ),
            "hotel_contexts": ("request_id", "hotel_id", "name", "address"),
        },
    ),
    "conversations.sqlite3": (
        1,
        {
            "conversations": (
                "id", "revision", "generation", "client_sequence", "draft", "expires",
            ),
            "operations": (
                "id", "conversation_id", "sequence", "generation", "base_revision",
                "fingerprint", "provider", "model", "status", "error_code",
            ),
        },
    ),
    "tuniu_http_attempts.sqlite3": (
        0,
        {"attempts": ("id", "at_epoch", "shanghai_day", "origin")},
    ),
}


class BackupError(Exception):
    """Snapshot was refused; existing personal data is never overwritten."""


def _label(text: str) -> str:
    if not SAFE_LABEL.fullmatch(text):
        raise BackupError("INVALID_LABEL")
    return text


def _runtime(root: Path) -> Path:
    return root.resolve() / "runtime"


def _status(path: Path, name: str) -> dict:
    if not path.is_file() or path.is_symlink():
        raise BackupError("MISSING_SOURCE")
    expected_version, tables = SCHEMAS[name]
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("INTEGRITY_FAILED")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise BackupError("FOREIGN_KEY_MISMATCH")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != expected_version:
                raise BackupError("SCHEMA_MISMATCH")
            counts = {}
            for table, columns in tables.items():
                found = tuple(c[1] for c in db.execute(f"PRAGMA table_info({table})"))
                if found != columns:
                    raise BackupError("SCHEMA_MISMATCH")
                counts[table] = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error as error:
        raise BackupError("DATABASE_UNREADABLE") from error
    return {"user_version": version, "tables": counts}


def _copy_online(source: Path, destination: Path) -> None:
    try:
        with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5)) as reader:
            with closing(sqlite3.connect(destination, timeout=5)) as writer:
                reader.backup(writer, pages=100, sleep=0.05)
                # A WAL-mode source produces a WAL-mode snapshot. Convert ONLY the
                # detached copy to a self-contained file; never alter the live source.
                mode = writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                if mode.lower() != "delete":
                    raise BackupError("SNAPSHOT_JOURNAL_MODE_FAILED")
    except sqlite3.Error as error:
        raise BackupError("SNAPSHOT_FAILED") from error


def _commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def create_backup(root: Path, label: str) -> Path:
    label = _label(label)
    runtime = _runtime(root)
    dest = runtime / "personal-backups" / label
    if dest.exists():
        raise BackupError("BACKUP_EXISTS")
    # Refuse incomplete runtime state before creating anything.
    for name in SCHEMAS:
        _status(runtime / name, name)
    dest.mkdir(parents=True, exist_ok=False)
    metadata = {}
    for name in SCHEMAS:
        source = runtime / name
        target = dest / name
        _copy_online(source, target)
        metadata[name] = _status(target, name)
    manifest = {
        "schema": FORMAT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _commit(root),
        "databases": metadata,
        "note": "Local SQLite snapshot, not a fresh provider or booking verification.",
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return dest


def verify_backup(root: Path, label: str) -> dict:
    folder = _runtime(root) / "personal-backups" / _label(label)
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise BackupError("MANIFEST_MISSING")
    try:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BackupError("MANIFEST_UNREADABLE") from error
    if (not isinstance(record, dict) or record.get("schema") != FORMAT
            or not isinstance(record.get("databases"), dict)
            or set(record["databases"]) != set(SCHEMAS)):
        raise BackupError("MANIFEST_INVALID")
    for name in SCHEMAS:
        db = folder / name
        if db.is_symlink() or not db.is_file():
            raise BackupError("BACKUP_DATABASE_MISSING")
        if _status(db, name) != record["databases"][name]:
            raise BackupError("COUNT_MISMATCH")
    return record


def restore_backup(root: Path, label: str) -> Path:
    label = _label(label)
    manifest = verify_backup(root, label)
    runtime = _runtime(root)
    target = runtime / "personal-restores" / label
    if target.exists():
        raise BackupError("RESTORE_EXISTS")
    target.mkdir(parents=True, exist_ok=False)
    source = runtime / "personal-backups" / label
    for name in SCHEMAS:
        shutil.copyfile(source / name, target / name)
        if _status(target / name, name) != manifest["databases"][name]:
            raise BackupError("RESTORE_MISMATCH")
    (target / "RESTORED.txt").write_text(
        "Isolated review copy only. NEVER replace active runtime databases or quota ledger.\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "verify", "restore"))
    parser.add_argument("--label", required=True, help="Safe ASCII label (A-Z, 0-9, hyphen, underscore)")
    args = parser.parse_args()
    try:
        if args.action == "create":
            path = create_backup(ROOT, args.label)
            print("SNAPSHOT_CREATED:", path)
        elif args.action == "verify":
            record = verify_backup(ROOT, args.label)
            print("SNAPSHOT_VALID:", args.label, "DATABASES:", len(record["databases"]))
        else:
            path = restore_backup(ROOT, args.label)
            print("ISOLATED_RESTORE_CREATED:", path)
        return 0
    except BackupError as error:
        print("BACKUP_REFUSED:", str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
