"""Short-lived structured conversation state; no raw prompts, outputs or credentials."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
from .condition_rules import empty_draft


#: Windows 上 WAL 模式的连接在 `-wal` 交接瞬间会偶发 SQLITE_READONLY 或
#: SQLITE_BUSY。这类错误可重试，且下面的写操作都有版本/状态守卫，重放不会重复生效。
TRANSIENT_MARKERS = ("readonly", "locked", "busy", "unable to open")
#: Every operation opens and closes its own connection, and closing the last
#: connection makes SQLite delete and later recreate ``-wal``/``-shm``. Under
#: IO contention (Windows antivirus, parallel tests) that handover is exactly
#: where a spurious SQLITE_READONLY appears, so the budget must survive a
#: slow patch, not just a single hiccup: 25+50+100+200 ms ≈ 375 ms total.
WRITE_ATTEMPTS = 4
RETRY_BASE_SECONDS = 0.025


class ConversationError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class StorageUnavailable(ConversationError):
    """Local state could not be written; must never be reported as model failure."""

    def __init__(self, detail=""):
        super().__init__("STATE_UNAVAILABLE")
        self.detail = detail


def transient(error):
    text = str(error).lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


class ConversationStore:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._with_retry(self._prepare)

    def _prepare(self, db):
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ConversationError("UNSUPPORTED_CONVERSATION_DATABASE")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, generation INTEGER NOT NULL, client_sequence INTEGER NOT NULL, draft TEXT NOT NULL, expires REAL NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, sequence INTEGER NOT NULL, generation INTEGER NOT NULL, base_revision INTEGER NOT NULL, fingerprint TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, UNIQUE(conversation_id,sequence))"
        )
        db.execute("PRAGMA user_version=1")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _with_retry(self, operation):
        """Run one transaction, retrying only transient WAL handover errors.

        A persistent failure becomes ``StorageUnavailable`` so callers cannot
        mistake a local disk/state problem for a model problem.
        """
        last = None
        for attempt in range(WRITE_ATTEMPTS):
            try:
                with self.db() as db:
                    return operation(db)
            except sqlite3.OperationalError as error:
                if not transient(error):
                    raise
                last = error
                time.sleep(RETRY_BASE_SECONDS * (2 ** attempt))
        raise StorageUnavailable(str(last))

    def create(self):
        def operation(db):
            ident = uuid4().hex
            db.execute("DELETE FROM conversations WHERE expires<=?", (self.clock(),))
            if db.execute("SELECT count(*) FROM conversations").fetchone()[0] >= 64:
                raise ConversationError("CONVERSATION_LIMIT")
            db.execute(
                "INSERT INTO conversations VALUES (?,0,0,0,?,?)",
                (ident, json.dumps(empty_draft(), ensure_ascii=False), self.clock() + 1800),
            )
            return ident

        return self._with_retry(operation)

    def load(self, ident):
        def operation(db):
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM conversations WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise ConversationError("CONVERSATION_NOT_FOUND")
            if row["expires"] <= self.clock():
                raise ConversationError("CONVERSATION_EXPIRED")
            value = dict(row)
            value["draft"] = json.loads(value["draft"])
            job = db.execute(
                "SELECT * FROM operations WHERE conversation_id=? ORDER BY sequence DESC LIMIT 1",
                (ident,),
            ).fetchone()
            value["operation"] = dict(job) if job else None
            return value

        return self._with_retry(operation)

    def operation(self, ident, opid):
        self.load(ident)
        with self.db() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (opid,)).fetchone()
            if row is not None and row["conversation_id"] != ident:
                raise ConversationError("OPERATION_CONFLICT")
            return dict(row) if row else None

    def start(self, ident, attempt, sequence, fingerprint):
        def operation(db):
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM conversations WHERE id=?", (ident,)).fetchone()
            if row is None or row["expires"] <= self.clock():
                raise ConversationError("CONVERSATION_EXPIRED")
            if row["revision"] != attempt.expected_revision:
                raise ConversationError("REVISION_CONFLICT")
            if sequence <= row["client_sequence"] or attempt.generation <= row["generation"]:
                raise ConversationError("STALE_OPERATION")
            if (
                db.execute(
                    "SELECT count(*) FROM operations WHERE conversation_id=?", (ident,)
                ).fetchone()[0]
                >= 64
            ):
                raise ConversationError("CONVERSATION_LIMIT")
            db.execute(
                "UPDATE operations SET status='superseded' WHERE conversation_id=? AND status IN ('queued','running','validating')",
                (ident,),
            )
            db.execute(
                "UPDATE conversations SET generation=?,client_sequence=?,expires=? WHERE id=?",
                (attempt.generation, sequence, self.clock() + 1800, ident),
            )
            db.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (
                    attempt.operation_id,
                    ident,
                    sequence,
                    attempt.generation,
                    attempt.expected_revision,
                    fingerprint,
                    attempt.route.provider,
                    attempt.route.model,
                    "queued",
                ),
            )

        return self._with_retry(operation)

    def mark(self, ident, attempt, status, error_code=None):
        def operation(db):
            return (
                db.execute(
                    "UPDATE operations SET status=?,error_code=? WHERE id=? AND conversation_id=? AND generation=? AND status IN ('queued','running','validating') AND EXISTS (SELECT 1 FROM conversations c WHERE c.id=? AND c.generation=? AND c.revision=?)",
                    (
                        status,
                        error_code,
                        attempt.operation_id,
                        ident,
                        attempt.generation,
                        ident,
                        attempt.generation,
                        attempt.expected_revision,
                    ),
                ).rowcount
                == 1
            )

        return self._with_retry(operation)

    def commit(self, ident, attempt, draft):
        def operation(db):
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE conversations SET draft=?,revision=revision+1,expires=? WHERE id=? AND generation=? AND revision=? AND expires>? AND EXISTS(SELECT 1 FROM operations o WHERE o.id=? AND o.conversation_id=? AND o.status='validating')",
                (
                    json.dumps(draft, ensure_ascii=False),
                    self.clock() + 1800,
                    ident,
                    attempt.generation,
                    attempt.expected_revision,
                    self.clock(),
                    attempt.operation_id,
                    ident,
                ),
            )
            if result.rowcount != 1:
                return False
            db.execute(
                "UPDATE operations SET status='succeeded' WHERE id=?", (attempt.operation_id,)
            )
            return True

        return self._with_retry(operation)

    def cancel(self, ident, opid, sequence, generation):
        def operation(db):
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM conversations WHERE id=?", (ident,)).fetchone()
            if row is None or row["expires"] <= self.clock():
                raise ConversationError("CONVERSATION_EXPIRED")
            old = db.execute("SELECT * FROM operations WHERE id=?", (opid,)).fetchone()
            if old and old["conversation_id"] != ident:
                raise ConversationError("OPERATION_CONFLICT")
            if not old:
                if (
                    db.execute(
                        "SELECT count(*) FROM operations WHERE conversation_id=?", (ident,)
                    ).fetchone()[0]
                    >= 64
                ):
                    raise ConversationError("CONVERSATION_LIMIT")
                db.execute(
                    "INSERT INTO operations VALUES (?,?,?,?,?,NULL,'deepseek','deepseek-flash','cancelled',NULL)",
                    (opid, ident, sequence, generation, row["revision"]),
                )
            else:
                db.execute(
                    "UPDATE operations SET status='cancelled' WHERE id=? AND status IN ('queued','running','validating')",
                    (opid,),
                )
            if sequence >= row["client_sequence"]:
                db.execute(
                    "UPDATE operations SET status='cancelled' WHERE conversation_id=? AND status IN ('queued','running','validating')",
                    (ident,),
                )
                db.execute(
                    "UPDATE conversations SET generation=?,client_sequence=? WHERE id=?",
                    (max(generation, row["generation"]), sequence, ident),
                )

        return self._with_retry(operation)

    def recover(self):
        def operation(db):
            db.execute(
                "UPDATE conversations SET generation=generation+1 WHERE id IN (SELECT conversation_id FROM operations WHERE status IN ('queued','running','validating'))"
            )
            db.execute(
                "UPDATE operations SET status='cancelled',error_code='SERVER_RESTARTED' WHERE status IN ('queued','running','validating')"
            )

        return self._with_retry(operation)
