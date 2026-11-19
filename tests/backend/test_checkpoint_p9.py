"""Offline P9 resume points: consistent snapshots, strict schema, isolated restore."""

from datetime import timedelta
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from stay_agent import checkpoint
from stay_agent.contracts import china_today
from stay_agent.offline_app import build_offline_app


def _client(root, runtime_root=None):
    return TestClient(
        build_offline_app(root=root, web_root=root / "web", runtime_root=runtime_root),
        base_url="http://127.0.0.1",
        raise_server_exceptions=True,
    )


def _query():
    start = china_today() + timedelta(days=7)
    return {"place": "广州", "check_in": start.isoformat(),
            "check_out": (start + timedelta(days=1)).isoformat()}


def _input():
    return {**_query(), "budget_fen": 20000}


def _commit_head(monkeypatch):
    monkeypatch.setattr(checkpoint, "_git_commit", lambda _root: "test-commit")


def _interpret(api, cid):
    api.post(
        f"/api/conversations/{cid}/operations",
        json={"operation_id": uuid4().hex, "expected_revision": 0,
              "client_sequence": 1,
              "user_inputs": ["广州，明天住1晚，1间房，每房2成人、无儿童，每房每晚200元以内。"],
              "provider": "deepseek"},
    )


def _checkpoint_record(tmp_path, label):
    return json.loads((tmp_path / "runtime/checkpoints" / label / "checkpoint.json")
                      .read_text(encoding="utf-8"))


def test_checkpoint_saves_both_committed_states_and_disconnection_time(tmp_path, monkeypatch):
    _commit_head(monkeypatch)
    api = _client(tmp_path)
    search = api.post("/api/search", json=_query()).json()
    cid = api.post("/api/conversations").json()["conversation_id"]
    _interpret(api, cid)
    directory = checkpoint.create_checkpoint(tmp_path, "unit-break")
    record = _checkpoint_record(tmp_path, "unit-break")
    assert record["schema"] == "xunqi-offline-checkpoint-v1"
    assert record["reason"] == "connection_lost"
    assert record["disconnected_at"].endswith("+00:00")
    assert record["app_commit"] == "test-commit"
    assert record["database"]["search"]["user_version"] == 2
    assert record["database"]["conversation"]["user_version"] == 1
    assert record["resume"]["search_sessions"][0]["request_id"] == search["request_id"]
    assert record["resume"]["conversations"][0]["revision"] == 1
    assert set(record["files"]) == {"search.sqlite3", "conversation.sqlite3"}
    assert not list(directory.glob("*-wal")) and not list(directory.glob("*-shm"))


def test_restored_app_continues_same_request_and_conversation_revision(tmp_path, monkeypatch):
    _commit_head(monkeypatch)
    api = _client(tmp_path)
    search = api.post("/api/search", json=_input()).json()
    request_id, hotel_id = search["request_id"], search["hotels"][0]["id"]
    cid = api.post("/api/conversations").json()["conversation_id"]
    _interpret(api, cid)
    checkpoint.create_checkpoint(tmp_path, "resume-break")
    restored = checkpoint.restore_checkpoint(tmp_path, "resume-break",
                                             expected_git_commit="test-commit")
    resumed = _client(tmp_path, runtime_root=restored)
    assert resumed.get(f"/api/conversations/{cid}").json()["revision"] == 1
    assert resumed.post(
        f"/api/hotels/{hotel_id}/quotes", json={"request_id": request_id}
    ).status_code == 200
    assert (restored / "RESTORED.json").is_file()
    assert (tmp_path / "runtime/offline_search.sqlite3").is_file()


@pytest.mark.parametrize("change", ["version", "table", "column"])
def test_invalid_schema_refuses_restore_and_preserves_target(tmp_path, monkeypatch, change):
    _commit_head(monkeypatch)
    _client(tmp_path).post("/api/search", json=_query())
    directory = checkpoint.create_checkpoint(tmp_path, "bad-schema")
    db = sqlite3.connect(directory / "search.sqlite3")
    if change == "version":
        db.execute("PRAGMA user_version=3")
    elif change == "table":
        db.execute("DROP TABLE hotel_contexts")
    else:
        db.execute("ALTER TABLE search_sessions RENAME COLUMN context_json TO context")
    db.commit()
    db.close()
    record = _checkpoint_record(tmp_path, "bad-schema")
    record["files"]["search.sqlite3"]["sha256"] = checkpoint._sha256(directory / "search.sqlite3")
    (directory / "checkpoint.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(checkpoint.CheckpointError) as error:
        checkpoint.restore_checkpoint(tmp_path, "bad-schema")
    assert str(error.value) in {
        "UNSUPPORTED_DATABASE_VERSION", "TABLE_MISSING", "TABLE_SCHEMA_MISMATCH",
    }
    assert not (tmp_path / "runtime/restored-bad-schema").exists()


def test_tampered_checkpoint_file_fails_closed(tmp_path, monkeypatch):
    _commit_head(monkeypatch)
    _client(tmp_path).post("/api/search", json=_query())
    directory = checkpoint.create_checkpoint(tmp_path, "tampered")
    with (directory / "conversation.sqlite3").open("ab") as target:
        target.write(b"tampered")
    with pytest.raises(checkpoint.CheckpointError, match="CHECKPOINT_FILE_MISMATCH"):
        checkpoint.restore_checkpoint(tmp_path, "tampered")


def test_restore_is_isolated_and_refuses_existing_target(tmp_path, monkeypatch):
    _commit_head(monkeypatch)
    _client(tmp_path).post("/api/search", json=_query())
    checkpoint.create_checkpoint(tmp_path, "safe-break")
    first = checkpoint.restore_checkpoint(tmp_path, "safe-break")
    assert (first / "offline_search.sqlite3").is_file()
    with pytest.raises(checkpoint.CheckpointError, match="RESTORE_TARGET_EXISTS"):
        checkpoint.restore_checkpoint(tmp_path, "safe-break")


def test_checkpoint_api_marks_disconnection_and_lists_resume_state(tmp_path, monkeypatch):
    _commit_head(monkeypatch)
    api = _client(tmp_path)
    response = api.post("/api/search", json=_query()).json()
    assert api.get("/api/checkpoint").json()["connected"] is True
    saved = api.post("/api/checkpoint").json()
    assert saved["status"] == "saved"
    assert saved["checkpoint"]["checkpoint_id"].startswith("break-")
    assert saved["checkpoint"]["disconnected_at"] is not None
    record = checkpoint.inspect_checkpoint(tmp_path, saved["checkpoint"]["checkpoint_id"])
    assert record["resume"]["search_sessions"][0]["request_id"] == response["request_id"]
    resumed = api.get("/api/checkpoint/resume").json()
    assert resumed["status"] == "ready"
    assert resumed["search"]["request_id"] == response["request_id"]
    assert len(resumed["search"]["hotels"]) > 0
