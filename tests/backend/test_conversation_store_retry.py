"""Local state failures must be retried when transient, and never look like model failures."""

from contextlib import contextmanager
import sqlite3

import pytest

from stay_agent.conversation_store import (
    WRITE_ATTEMPTS,
    ConversationStore,
    StorageUnavailable,
)


TRANSIENT = "attempt to write a readonly database"


def flaky(store, failures):
    """Replace the connection factory: the first ``failures`` calls raise, then it works."""

    original = store.db
    state = {"calls": 0}

    @contextmanager
    def db():
        state["calls"] += 1
        if state["calls"] <= failures:
            raise sqlite3.OperationalError(TRANSIENT)
        with original() as value:
            yield value

    store.db = db
    return state


def broken(store, message=TRANSIENT):
    @contextmanager
    def db():
        raise sqlite3.OperationalError(message)

    store.db = db


def test_transient_readonly_write_is_retried_and_succeeds(tmp_path):
    store = ConversationStore(tmp_path / "state.db")
    state = flaky(store, failures=2)

    ident = store.create()

    assert ident
    assert state["calls"] == 3
    assert store.load(ident)["revision"] == 0


def test_persistent_readonly_write_raises_storage_unavailable(tmp_path):
    store = ConversationStore(tmp_path / "state.db")
    broken(store)

    with pytest.raises(StorageUnavailable) as caught:
        store.create()

    assert caught.value.code == "STATE_UNAVAILABLE"


def test_non_transient_sqlite_error_is_not_hidden(tmp_path):
    store = ConversationStore(tmp_path / "state.db")
    broken(store, message="no such table: conversations")

    with pytest.raises(sqlite3.OperationalError):
        store.create()


def test_retry_is_bounded(tmp_path):
    store = ConversationStore(tmp_path / "state.db")
    state = flaky(store, failures=99)

    with pytest.raises(StorageUnavailable):
        store.create()

    # Pinned to the constant on purpose: retrying forever would hang a request
    # instead of admitting the failure, and a silent widening would hide cost.
    assert state["calls"] == WRITE_ATTEMPTS


def test_the_retry_budget_survives_a_slow_io_patch_not_just_one_hiccup(tmp_path):
    """r68: a full-suite run once reported a model rate limit as STATE_UNAVAILABLE.

    Every operation opens and closes its own connection, so closing the last one
    makes SQLite delete ``-wal``/``-shm``; under IO contention that handover can
    fail more than twice in a row. Three failures must still recover.
    """
    store = ConversationStore(tmp_path / "state.db")
    state = flaky(store, failures=3)

    ident = store.create()

    assert ident
    assert state["calls"] == 4
