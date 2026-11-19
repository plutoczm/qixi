"""Process-local generation fence. Not exposed as a production conversation API yet."""

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
import threading
from uuid import uuid4


class GenerationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Status(StrEnum):
    RUNNING = "running"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


ACTIVE = frozenset({Status.RUNNING, Status.VALIDATING})


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str
    schema_version: str = "conditions.v1"

    def __post_init__(self):
        if (
            self.provider not in ("deepseek", "pi_chatgpt")
            or not self.model
            or not self.schema_version
        ):
            raise GenerationError("INVALID_ROUTE")


@dataclass(frozen=True)
class Attempt:
    operation_id: str
    generation: int
    expected_revision: int
    route: ModelRoute
    base_conditions: dict
    user_inputs: tuple[str, ...]
    cancelled: threading.Event = field(compare=False, repr=False)


class GenerationFence:
    """One conversation's lock protects cancellation, replacement and commit atomically.

    Raw model output never enters this object. The caller must finish validation
    before commit. Cancellation fences local state even when a provider ignores abort.
    Persistence and request/session ownership must be supplied by the API integration.
    """

    def __init__(
        self, conditions=None, revision=0, max_pending_inputs=6, max_operations=64, generation=0
    ):
        self._lock = threading.Lock()
        self._conditions = deepcopy(conditions or {})
        self._revision = revision
        self._generation = generation
        self._current = None
        self._status = None
        self._pending_inputs = ()
        self._max_pending_inputs = max_pending_inputs
        self._max_operations = max_operations
        self._started = 0
        self._terminal = {}

    def snapshot(self):
        with self._lock:
            return {
                "revision": self._revision,
                "generation": self._generation,
                "conditions": deepcopy(self._conditions),
                "status": self._status,
                "operation_id": self._current.operation_id if self._current else None,
            }

    def begin(self, user_input, expected_revision, route, operation_id=None):
        if not isinstance(route, ModelRoute):
            raise GenerationError("INVALID_ROUTE")
        if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > 2000:
            raise GenerationError("INVALID_INPUT")
        with self._lock:
            if type(expected_revision) is not int or expected_revision != self._revision:
                raise GenerationError("REVISION_CONFLICT")
            if self._started >= self._max_operations:
                raise GenerationError("CONVERSATION_OPERATION_LIMIT")
            pending = self._pending_inputs + (user_input,)
            if len(pending) > self._max_pending_inputs:
                raise GenerationError("PENDING_INPUT_LIMIT")
            if self._status in ACTIVE:
                self._current.cancelled.set()
                self._terminal[self._current.operation_id] = Status.SUPERSEDED
            self._generation += 1
            self._started += 1
            self._pending_inputs = pending
            attempt = Attempt(
                operation_id or uuid4().hex,
                self._generation,
                self._revision,
                route,
                deepcopy(self._conditions),
                pending,
                threading.Event(),
            )
            self._current, self._status = attempt, Status.RUNNING
            return attempt

    def _owns(self, attempt):
        return (
            self._current is attempt
            and self._generation == attempt.generation
            and self._revision == attempt.expected_revision
        )

    def status(self, attempt):
        with self._lock:
            if not self._owns(attempt):
                return self._terminal.get(attempt.operation_id, Status.SUPERSEDED)
            return self._status

    def validating(self, attempt):
        with self._lock:
            if (
                not self._owns(attempt)
                or self._status != Status.RUNNING
                or attempt.cancelled.is_set()
            ):
                return False
            self._status = Status.VALIDATING
            return True

    def commit(self, attempt, validated_conditions):
        # Validation is deliberately outside this fence: no raw/partial output is accepted.
        if not isinstance(validated_conditions, dict):
            raise GenerationError("INVALID_CONDITIONS")
        with self._lock:
            if (
                not self._owns(attempt)
                or self._status != Status.VALIDATING
                or attempt.cancelled.is_set()
            ):
                return False
            self._conditions = deepcopy(validated_conditions)
            self._revision += 1
            self._status = Status.SUCCEEDED
            self._terminal[attempt.operation_id] = Status.SUCCEEDED
            self._pending_inputs = ()
            return True

    def stop(self, operation_id):
        with self._lock:
            if not self._current or self._current.operation_id != operation_id:
                return False
            if self._status not in ACTIVE:
                return False
            self._current.cancelled.set()
            self._status = Status.CANCELLED
            self._generation += 1
            self._terminal[self._current.operation_id] = Status.CANCELLED
            self._pending_inputs = ()
            # The terminal status participates in the same atomic fence as commit.
            return True

    def fail(self, attempt, timed_out=False):
        with self._lock:
            if not self._owns(attempt) or self._status not in ACTIVE:
                return False
            self._status = Status.TIMED_OUT if timed_out else Status.FAILED
            self._terminal[attempt.operation_id] = self._status
            self._current.cancelled.set()
            self._pending_inputs = ()
            return True

    def reset(self, expected_revision):
        with self._lock:
            if type(expected_revision) is not int or expected_revision != self._revision:
                raise GenerationError("REVISION_CONFLICT")
            if self._current and self._status in ACTIVE:
                self._current.cancelled.set()
                self._terminal[self._current.operation_id] = Status.SUPERSEDED
            self._generation += 1
            self._revision += 1
            self._conditions = {}
            self._pending_inputs = ()
            self._current, self._status = None, None
