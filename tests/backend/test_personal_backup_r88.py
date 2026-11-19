"""R88 personal SQLite backup must be consistent, local, and never overwrite live data."""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.personal_backup import BackupError, create_backup, restore_backup, verify_backup


def _seed(root: Path) -> Path:
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    schemas = {
        "search.sqlite3": (
            2,
            [
                "CREATE TABLE search_sessions(request_id TEXT PRIMARY KEY,created_at REAL NOT NULL,expires_at REAL NOT NULL,context_json TEXT NOT NULL,hotel_ids_json TEXT NOT NULL)",
                "CREATE TABLE hotel_contexts(request_id TEXT NOT NULL,hotel_id INTEGER NOT NULL,name TEXT NOT NULL,address TEXT,PRIMARY KEY(request_id,hotel_id),FOREIGN KEY(request_id) REFERENCES search_sessions(request_id))",
                "INSERT INTO search_sessions VALUES('example-request',1,2,'{\"place\":\"广州\"}','[5]')",
                "INSERT INTO hotel_contexts VALUES('example-request',5,'测试旅店','样例地址')",
            ],
        ),
        "conversations.sqlite3": (
            1,
            [
                "CREATE TABLE conversations(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,generation INTEGER NOT NULL,client_sequence INTEGER NOT NULL,draft TEXT NOT NULL,expires REAL NOT NULL)",
                "CREATE TABLE operations(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES conversations(id),sequence INTEGER NOT NULL,generation INTEGER NOT NULL,base_revision INTEGER NOT NULL,fingerprint TEXT,provider TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,error_code TEXT,UNIQUE(conversation_id,sequence))",
                "INSERT INTO conversations VALUES('example-conversation',1,0,1,'{\"secret\":\"not-for-manifest\"}',999)",
            ],
        ),
        "tuniu_http_attempts.sqlite3": (
            0,
            [
                "CREATE TABLE attempts(id INTEGER PRIMARY KEY,at_epoch REAL NOT NULL,shanghai_day TEXT NOT NULL,origin TEXT NOT NULL DEFAULT '')",
                "INSERT INTO attempts VALUES(1,1.5,'2026-11-12','')",
                "INSERT INTO attempts VALUES(2,2.5,'2026-11-12','')",
            ],
        ),
    }
    for name, (version, statements) in schemas.items():
        with closing(sqlite3.connect(runtime / name)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            for statement in statements:
                db.execute(statement)
            db.execute(f"PRAGMA user_version={version}")
    return runtime


def _count(path: Path, table: str) -> int:
    with closing(sqlite3.connect(path)) as db:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_creates_online_sqlite_snapshots_and_a_metadata_only_manifest(tmp_path):
    runtime = _seed(tmp_path)
    snapshot = create_backup(tmp_path, "personal-r88")
    assert snapshot == runtime / "personal-backups" / "personal-r88"
    assert {item.name for item in snapshot.iterdir()} == {
        "search.sqlite3", "conversations.sqlite3",
        "tuniu_http_attempts.sqlite3", "manifest.json",
    }
    manifest_text = (snapshot / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["schema"] == "xunqi-personal-backup-v1"
    assert set(manifest["databases"]) == {
        "search.sqlite3", "conversations.sqlite3", "tuniu_http_attempts.sqlite3",
    }
    assert "广州" not in manifest_text and "not-for-manifest" not in manifest_text
    assert "api_key" not in manifest_text.lower()
    assert _count(snapshot / "tuniu_http_attempts.sqlite3", "attempts") == 2
    assert verify_backup(tmp_path, "personal-r88")["databases"] == manifest["databases"]


def test_restore_is_isolated_and_never_overwrites_live_sqlite_or_rate_ledger(tmp_path):
    runtime = _seed(tmp_path)
    create_backup(tmp_path, "one")
    with sqlite3.connect(runtime / "tuniu_http_attempts.sqlite3") as db:
        db.execute("INSERT INTO attempts VALUES(3,3.5,'2026-11-12','')")
    restored = restore_backup(tmp_path, "one")
    assert restored == runtime / "personal-restores" / "one"
    assert _count(restored / "tuniu_http_attempts.sqlite3", "attempts") == 2
    assert _count(runtime / "tuniu_http_attempts.sqlite3", "attempts") == 3
    assert _count(restored / "search.sqlite3", "search_sessions") == 1
    with pytest.raises(BackupError, match="RESTORE_EXISTS"):
        restore_backup(tmp_path, "one")
    assert _count(runtime / "tuniu_http_attempts.sqlite3", "attempts") == 3


def test_refuses_existing_backup_without_overwriting(tmp_path):
    _seed(tmp_path)
    first = create_backup(tmp_path, "one")
    with pytest.raises(BackupError, match="BACKUP_EXISTS"):
        create_backup(tmp_path, "one")
    assert verify_backup(tmp_path, "one")["schema"] == "xunqi-personal-backup-v1"
    assert first.exists()


@pytest.mark.parametrize("label", ["../outside", "../", "a/b", "a\\b", ".", "", "空格 目录", "_hidden"])
def test_rejects_unsafe_backup_labels(tmp_path, label):
    _seed(tmp_path)
    with pytest.raises(BackupError, match="INVALID_LABEL"):
        create_backup(tmp_path, label)
    assert not (tmp_path / "outside").exists()


def test_missing_quota_ledger_fails_closed_before_creating_any_backup(tmp_path):
    runtime = _seed(tmp_path)
    (runtime / "tuniu_http_attempts.sqlite3").unlink()
    with pytest.raises(BackupError, match="MISSING_SOURCE"):
        create_backup(tmp_path, "incomplete")
    assert not (runtime / "personal-backups" / "incomplete").exists()


def test_tampered_backup_row_counts_or_schema_are_rejected_without_restoring(tmp_path):
    runtime = _seed(tmp_path)
    snapshot = create_backup(tmp_path, "tampered")
    with sqlite3.connect(snapshot / "tuniu_http_attempts.sqlite3") as db:
        db.execute("DELETE FROM attempts")
    with pytest.raises(BackupError, match="COUNT_MISMATCH"):
        verify_backup(tmp_path, "tampered")
    with pytest.raises(BackupError, match="COUNT_MISMATCH"):
        restore_backup(tmp_path, "tampered")
    assert not (runtime / "personal-restores" / "tampered").exists()


def test_source_database_schema_change_cannot_be_backed_up(tmp_path):
    runtime = _seed(tmp_path)
    with sqlite3.connect(runtime / "search.sqlite3") as db:
        db.execute("DROP TABLE hotel_contexts")
    with pytest.raises(BackupError, match="SCHEMA_MISMATCH"):
        create_backup(tmp_path, "bad-schema")


def test_malformed_manifest_structure_is_rejected_not_an_uncaught_type_error(tmp_path):
    _seed(tmp_path)
    snapshot = create_backup(tmp_path, "bad-manifest")
    path = snapshot / "manifest.json"
    path.write_text(json.dumps({"schema": "xunqi-personal-backup-v1", "databases": None}), encoding="utf-8")
    with pytest.raises(BackupError, match="MANIFEST_INVALID"):
        verify_backup(tmp_path, "bad-manifest")
