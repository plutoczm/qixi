"""Deterministic cancellation/late-output races; no models or credentials."""

from dataclasses import FrozenInstanceError
import threading
import pytest
from stay_agent.generation import GenerationFence, GenerationError, ModelRoute, Status

ROUTE = ModelRoute("deepseek", "deepseek-flash")


def attempt(fence, text="广州", revision=0):
    return fence.begin(text, revision, ROUTE)


def test_stop_during_generation_never_changes_last_committed_conditions():
    fence = GenerationFence({"place": "广州", "budget_fen": 20000})
    job = attempt(fence)
    assert fence.stop(job.operation_id) and job.cancelled.is_set()
    assert not fence.validating(job)
    assert not fence.commit(job, {"place": "杭州"})
    assert fence.snapshot()["conditions"] == {"place": "广州", "budget_fen": 20000}
    assert fence.snapshot()["revision"] == 0
    assert fence.status(job) == Status.CANCELLED


def test_replacement_retains_pending_user_intent_but_discards_old_model_result():
    fence = GenerationFence({"check_in": "2026-11-07", "rooms": 1})
    old = attempt(fence, "去广州，预算每晚200")
    new = attempt(fence, "临时改杭州")
    assert old.cancelled.is_set() and fence.status(old) == Status.SUPERSEDED
    assert new.user_inputs == ("去广州，预算每晚200", "临时改杭州")
    assert new.base_conditions == {"check_in": "2026-11-07", "rooms": 1}
    assert not fence.validating(old) and not fence.commit(old, {"place": "广州"})
    assert fence.validating(new)
    assert fence.commit(new, {"place": "杭州", "budget_fen": 20000, "rooms": 1})
    assert fence.snapshot()["revision"] == 1


def test_validation_does_not_commit_and_cancel_wins_before_commit():
    fence = GenerationFence({"place": "广州"})
    job = attempt(fence)
    assert fence.validating(job)
    assert fence.snapshot()["revision"] == 0
    assert fence.stop(job.operation_id)
    assert not fence.commit(job, {"place": "杭州"})
    assert fence.snapshot()["conditions"]["place"] == "广州"


def test_old_revision_rejected_before_it_can_cancel_newer_work():
    fence = GenerationFence(revision=2)
    current = attempt(fence, revision=2)
    with pytest.raises(GenerationError, match="REVISION_CONFLICT"):
        attempt(fence, revision=1)
    assert not current.cancelled.is_set() and fence.status(current) == Status.RUNNING


def test_duplicate_done_is_idempotent_and_late_cancel_does_not_undo_success():
    fence = GenerationFence()
    job = attempt(fence)
    assert not fence.commit(job, {"place": "杭州"})  # Missing validating transition.
    assert fence.validating(job) and fence.commit(job, {"place": "广州"})
    assert not fence.commit(job, {"place": "杭州"})
    assert not fence.stop(job.operation_id)
    assert fence.status(job) == Status.SUCCEEDED
    assert fence.snapshot()["revision"] == 1


@pytest.mark.parametrize("timeout", [False, True])
def test_failed_or_timed_out_output_cannot_commit_or_leak_partial_state(timeout):
    fence = GenerationFence({"place": "广州"})
    job = attempt(fence)
    assert fence.fail(job, timed_out=timeout)
    assert fence.status(job) == (Status.TIMED_OUT if timeout else Status.FAILED)
    assert not fence.validating(job) and not fence.commit(job, {"place": "杭州"})
    assert fence.snapshot()["conditions"] == {"place": "广州"}


def test_wrong_operation_stop_cannot_cancel_current_job():
    fence = GenerationFence()
    old = attempt(fence)
    new = attempt(fence)
    assert not fence.stop(old.operation_id)
    assert not new.cancelled.is_set()


def test_reset_invalidates_running_results_and_clears_pending_intent():
    fence = GenerationFence({"place": "广州"})
    old = attempt(fence)
    fence.reset(0)
    assert old.cancelled.is_set()
    assert not fence.commit(old, {"place": "杭州"})
    new = attempt(fence, "成都", revision=1)
    assert new.base_conditions == {} and new.user_inputs == ("成都",)


def test_route_and_conditions_snapshots_cannot_mutate_committed_state():
    source = {"children_ages": [5]}
    fence = GenerationFence(source)
    source["children_ages"].append(9)
    job = attempt(fence)
    job.base_conditions["children_ages"].append(10)
    assert fence.snapshot()["conditions"] == {"children_ages": [5]}
    with pytest.raises(FrozenInstanceError):
        job.route.provider = "pi_codex"


def test_pending_input_cap_rejects_before_superseding_active_request():
    fence = GenerationFence(max_pending_inputs=1)
    first = attempt(fence)
    with pytest.raises(GenerationError, match="PENDING_INPUT_LIMIT"):
        attempt(fence)
    assert not first.cancelled.is_set()


def test_cancel_commit_race_has_exactly_one_terminal_result():
    for _ in range(30):
        fence = GenerationFence({"place": "广州"})
        job = attempt(fence)
        assert fence.validating(job)
        barrier = threading.Barrier(3)
        results = {}

        def commit():
            barrier.wait()
            results["commit"] = fence.commit(job, {"place": "杭州"})

        def cancel():
            barrier.wait()
            results["cancel"] = fence.stop(job.operation_id)

        threads = [threading.Thread(target=commit), threading.Thread(target=cancel)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
        assert sum(results.values()) == 1
        state = fence.snapshot()
        assert (state["revision"], state["conditions"]["place"]) == (
            (1, "杭州") if results["commit"] else (0, "广州")
        )


def test_success_and_cancel_terminal_states_survive_a_later_turn():
    fence = GenerationFence()
    first = attempt(fence)
    assert fence.validating(first) and fence.commit(first, {"place": "广州"})
    second = attempt(fence, "杭州", revision=1)
    assert fence.status(first) == Status.SUCCEEDED
    assert fence.stop(second.operation_id)
    third = attempt(fence, "成都", revision=1)
    assert fence.status(second) == Status.CANCELLED
    assert fence.status(first) == Status.SUCCEEDED
    assert fence.status(third) == Status.RUNNING


def test_operation_history_cap_is_bounded_and_does_not_cancel_active_work():
    fence = GenerationFence(max_operations=1)
    first = attempt(fence)
    with pytest.raises(GenerationError, match="CONVERSATION_OPERATION_LIMIT"):
        attempt(fence)
    assert not first.cancelled.is_set()
