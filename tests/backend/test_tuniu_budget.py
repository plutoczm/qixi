"""Offline tests for persistent provider-wide *local-project* HTTP budget."""
from datetime import datetime

import pytest

from stay_agent.providers.data.rollinggo_client import RollingGoError
from stay_agent.providers.data.tuniu_budget import DurableRequestGate, ZONE


def test_minute_limit_survives_new_instances_and_allows_after_sixty_seconds(tmp_path):
    now = [datetime(2026, 11, 2, 12, tzinfo=ZONE).timestamp()]
    path = tmp_path / "requests.sqlite3"
    a = DurableRequestGate(path, clock=lambda: now[0])
    b = DurableRequestGate(path, clock=lambda: now[0])
    for i in range(5):
        (a if i % 2 else b).reserve()
    assert a.known_count() == b.known_count() == 5
    with pytest.raises(RollingGoError, match="RATE_LIMITED"):
        b.reserve()
    assert a.known_count() == 5
    now[0] += 61
    b.reserve()
    assert a.known_count() == 6


def test_daily_limit_persists_and_resets_only_on_shanghai_midnight(tmp_path):
    now = [datetime(2026, 11, 2, 10, tzinfo=ZONE).timestamp()]
    gate = DurableRequestGate(tmp_path / "requests.sqlite3", clock=lambda: now[0])
    for _ in range(50):
        gate.reserve()
        now[0] += 61
    assert gate.known_count(day="2026-11-02") == 50
    with pytest.raises(RollingGoError, match="REQUEST_LIMIT"):
        gate.reserve()
    assert gate.known_count(day="2026-11-02") == 50
    now[0] = datetime(2026, 11, 3, 0, 1, tzinfo=ZONE).timestamp()
    gate.reserve()
    assert gate.known_count(day="2026-11-03") == 1
    assert gate.known_count(day="2026-11-02") == 50


def test_seed_prior_known_http_exactly_once_without_supplier_call(tmp_path):
    now = [datetime(2026, 11, 2, 20, tzinfo=ZONE).timestamp()]
    gate = DurableRequestGate(tmp_path / "requests.sqlite3", clock=lambda: now[0])
    for i in range(5):
        gate.seed_historical(
            tag=f"r45-{i+1:02d}",
            day="2026-11-02",
            attempted_epoch=now[0] - 3600,
        )
    for i in range(5):
        gate.seed_historical(
            tag=f"r45-{i+1:02d}",
            day="2026-11-02",
            attempted_epoch=now[0] - 3600,
        )
    assert gate.known_count() == 5
    gate.reserve()
    assert gate.known_count() == 6


def test_corrupted_sqlite_fail_closed(tmp_path):
    path = tmp_path / "requests.sqlite3"
    path.write_bytes(b"not a sqlite file")
    gate = DurableRequestGate(path)
    with pytest.raises(RollingGoError, match="REQUEST_LIMIT"):
        gate.reserve()
