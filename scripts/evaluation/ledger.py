"""Persistent before-send counts shared by Python and Node evaluation processes."""

import json
from pathlib import Path
import sqlite3
import sys


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def create(cls, path, limits):
        path = Path(path)
        if path.exists():
            raise FileExistsError("RUN_ALREADY_EXISTS")
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE limits(source TEXT PRIMARY KEY,maximum INTEGER,stopped INTEGER DEFAULT 0)"
            )
            db.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY,source TEXT,status INTEGER)")
            db.executemany("INSERT INTO limits(source,maximum) VALUES (?,?)", limits.items())
        return cls(path)

    def connect(self):
        # A missing ledger must fail closed, never create a new empty allowance.
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)

    def reserve(self, source):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT maximum,stopped FROM limits WHERE source=?", (source,)
            ).fetchone()
            count = db.execute(
                "SELECT count(*) FROM attempts WHERE source=?", (source,)
            ).fetchone()[0]
            if row is None or row[1] or count >= row[0]:
                raise BudgetExceeded("REQUEST_LIMIT")
            return db.execute("INSERT INTO attempts(source) VALUES (?)", (source,)).lastrowid

    def finish(self, ident, status):
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("INVALID_STATUS")
        with self.connect() as db:
            db.execute(
                "UPDATE attempts SET status=? WHERE id=? AND status IS NULL", (status, ident)
            )
            if status in {401, 402, 403, 429}:
                db.execute(
                    "UPDATE limits SET stopped=1 WHERE source=(SELECT source FROM attempts WHERE id=?)",
                    (ident,),
                )

    def stop(self, source):
        with self.connect() as db:
            db.execute("UPDATE limits SET stopped=1 WHERE source=?", (source,))

    def summary(self):
        with self.connect() as db:
            return {
                source: dict(
                    limit=maximum,
                    attempted=count,
                    http_received=received,
                    http_ok=ok,
                    stopped=bool(stopped),
                )
                for source, maximum, stopped, count, received, ok in db.execute(
                    "SELECT l.source,l.maximum,l.stopped,count(a.id),count(a.status),"
                    "sum(CASE WHEN a.status BETWEEN 200 AND 299 THEN 1 ELSE 0 END) "
                    "FROM limits l LEFT JOIN attempts a ON a.source=l.source GROUP BY l.source"
                )
            }


if __name__ == "__main__":
    try:
        budget = Budget(sys.argv[2])
        if sys.argv[1] == "reserve":
            print(budget.reserve(sys.argv[3]))
        elif sys.argv[1] == "finish":
            budget.finish(int(sys.argv[3]), int(sys.argv[4]))
        elif sys.argv[1] == "summary":
            print(json.dumps(budget.summary()))
        else:
            raise ValueError("UNKNOWN_OPERATION")
    except Exception:
        print("LEDGER_REFUSED", file=sys.stderr)
        raise SystemExit(2)
