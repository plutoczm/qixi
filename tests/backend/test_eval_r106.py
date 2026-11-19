"""Evaluation guardrails: these tests never access providers."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.evaluation.ledger import Budget, BudgetExceeded
from scripts.evaluation.scoring import score_turn


def test_budget_is_persistent_atomic_and_rejects_unknown_sources(tmp_path):
    path = tmp_path / "budget.sqlite"
    budget = Budget.create(path, {"deepseek": 2})
    first = budget.reserve("deepseek")
    budget.finish(first, 200)
    Budget(path).reserve("deepseek")
    with pytest.raises(BudgetExceeded):
        budget.reserve("deepseek")
    with pytest.raises(BudgetExceeded):
        budget.reserve("other")
    assert budget.summary()["deepseek"] == {
        "limit": 2,
        "attempted": 2,
        "http_received": 1,
        "http_ok": 1,
        "stopped": False,
    }


def test_parallel_reservations_never_exceed_limit(tmp_path):
    budget = Budget.create(tmp_path / "budget.sqlite", {"amap": 3})

    def attempt(_):
        try:
            budget.reserve("amap")
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 3
    budget.stop("amap")
    with pytest.raises(BudgetExceeded):
        budget.reserve("amap")


def test_scoring_checks_preservation_and_does_not_turn_missing_into_pass():
    prior = {"draft": {"place": "广州", "rooms": 2}}
    row = {
        "status": "succeeded",
        "error": None,
        "query_ready": True,
        "draft": {"place": "广州", "rooms": 1},
    }
    result = score_turn(row, {"place": "广州", "preserve": ["rooms"], "clarify": "儿童年龄"}, prior)
    assert result["passed"] == 1 and result["failed"] == 1
    assert result["requires_review"] == ["clarify"]
    assert result["checks"][1]["passed"] is False


def test_failed_operation_is_not_accurate_even_if_old_fields_match():
    row = {"status": "failed", "error": "TIMEOUT", "query_ready": False, "draft": {"place": "广州"}}
    result = score_turn(row, {"place": "广州"})
    assert result["passed"] == 0 and result["failed"] == 1


def test_absent_factual_fields_and_absent_preservation_are_failures():
    row = {"status": "succeeded", "error": None, "draft": {}}
    result = score_turn(row, {"place": "广州", "preserve": ["rooms"]}, {"draft": {}})
    assert result["failed"] == 2 and result["passed"] == 0
    assert result["requires_review"] == []


def test_fatal_http_status_stops_shared_budget_for_all_processes(tmp_path):
    path = tmp_path / "budget.sqlite"
    budget = Budget.create(path, {"deepseek": 80})
    budget.finish(budget.reserve("deepseek"), 429)
    assert Budget(path).summary()["deepseek"]["stopped"] is True
    with pytest.raises(BudgetExceeded):
        Budget(path).reserve("deepseek")


def test_explicit_null_is_not_missing_fact():
    missing = {"status": "succeeded", "error": None, "draft": {}}
    explicit = {**missing, "draft": {"place": None}}
    assert score_turn(missing, {"place": None})["failed"] == 1
    assert score_turn(explicit, {"place": None})["passed"] == 1
