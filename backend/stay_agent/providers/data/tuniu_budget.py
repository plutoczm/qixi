"""Project-local SQLite quota ledger for raw Tuniu MCP HTTP attempts.

A request is counted *before* the HTTPS call, even if its connection fails.
The ledger is shared across this project's Windows/Linux processes and survives
restarts; it cannot see usage from other apps or API keys outside this project.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import math
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

from .rollinggo_client import RollingGoError

ROOT = Path(__file__).resolve().parents[4]
LEDGER_PATH = ROOT / "runtime" / "tuniu_http_attempts.sqlite3"
ZONE = ZoneInfo("Asia/Shanghai")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY,
  at_epoch REAL NOT NULL,
  shanghai_day TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS attempts_origin ON attempts(origin)
WHERE origin <> '';
"""


class DurableRequestGate:
    """Fail closed on lock, corruption or budget exhaustion (5/min; 50/day)."""

    def __init__(self, path: Path = LEDGER_PATH, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout=2000")
        db.executescript(_SCHEMA)
        return db

    def reserve(self) -> None:
        try:
            now = float(self.clock())
            if not math.isfinite(now):
                raise ValueError("invalid clock")
            day = datetime.fromtimestamp(now, ZONE).date().isoformat()
            with closing(self._open()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                minute = db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE at_epoch > ?",
                    (now - 60,),
                ).fetchone()[0]
                daily = db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE shanghai_day = ?",
                    (day,),
                ).fetchone()[0]
                if minute >= 5:
                    raise RollingGoError("RATE_LIMITED")
                if daily >= 50:
                    raise RollingGoError("REQUEST_LIMIT")
                db.execute(
                    "INSERT INTO attempts (at_epoch, shanghai_day) VALUES (?, ?)",
                    (now, day),
                )
                db.commit()
        except RollingGoError:
            raise
        except (OSError, ValueError, OverflowError, sqlite3.Error):
            # No HTTP if ledger missing, locked, corrupt or read-only.
            raise RollingGoError("REQUEST_LIMIT") from None

    def known_count(self, *, day: str | None = None) -> int:
        """Offline budget inspection; does not call the supplier."""
        try:
            today = day or datetime.fromtimestamp(self.clock(), ZONE).date().isoformat()
            with closing(self._open()) as db, db:
                return int(db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE shanghai_day = ?",
                    (today,),
                ).fetchone()[0])
        except (OSError, ValueError, sqlite3.Error):
            raise RollingGoError("REQUEST_LIMIT") from None

    def seed_historical(self, *, tag: str, day: str, attempted_epoch: float) -> None:
        """Idempotently import a *previously documented* attempt; no new HTTP.

        Names must be fixed local audit tags, never supplier or user payloads.
        """
        if (not tag.startswith("r45-") or len(tag) > 24 or not tag.isascii()
                or len(day) != 10 or not math.isfinite(attempted_epoch)):
            raise ValueError("invalid local audit seed")
        try:
            with closing(self._open()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT OR IGNORE INTO attempts(at_epoch, shanghai_day, origin)"
                    " VALUES (?, ?, ?)",
                    (float(attempted_epoch), day, tag),
                )
                db.commit()
        except (OSError, sqlite3.Error):
            raise RollingGoError("REQUEST_LIMIT") from None
