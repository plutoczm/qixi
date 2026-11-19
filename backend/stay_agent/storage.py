"""SQLite stores request context and membership only, never prices or links."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


class SessionError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class SessionStore:
    def __init__(self, path, ttl_seconds=1800, clock=time.time):
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise SessionError("UNSUPPORTED_DATABASE_VERSION")
            if version == 1:
                backup_path = self.path.with_name(
                    self.path.stem + ".v1-backup-" + uuid4().hex[:10] + ".sqlite3"
                )
                backup = sqlite3.connect(backup_path)
                try:
                    connection.backup(backup)
                finally:
                    backup.close()
            connection.execute(
                "CREATE TABLE IF NOT EXISTS search_sessions (request_id TEXT PRIMARY KEY, "
                "created_at REAL NOT NULL, expires_at REAL NOT NULL, context_json TEXT NOT NULL, "
                "hotel_ids_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS hotel_contexts (request_id TEXT NOT NULL, hotel_id INTEGER NOT NULL, "
                "name TEXT NOT NULL, address TEXT, PRIMARY KEY(request_id,hotel_id), "
                "FOREIGN KEY(request_id) REFERENCES search_sessions(request_id) ON DELETE CASCADE)"
            )
            connection.execute("PRAGMA user_version=2")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, request_id, context, hotel_ids, hotel_metadata=None, *, supplier_source=None):
        context = {key: value for key, value in context.items() if key != "_supplier_source"}
        if supplier_source in {"rollinggo", "tuniu"}:
            context["_supplier_source"] = supplier_source
        now = self.clock()
        with self._connect() as connection:
            connection.execute("DELETE FROM search_sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                "INSERT INTO search_sessions VALUES (?, ?, ?, ?, ?)",
                (
                    request_id,
                    now,
                    now + self.ttl_seconds,
                    json.dumps(context, ensure_ascii=False),
                    json.dumps(hotel_ids),
                ),
            )

            for hotel in hotel_metadata or []:
                if hotel["id"] in hotel_ids:
                    connection.execute(
                        "INSERT INTO hotel_contexts VALUES (?, ?, ?, ?)",
                        (request_id, hotel["id"], hotel["name"], hotel.get("address")),
                    )

    def hotel(self, request_id, hotel_id):
        self.load(request_id, hotel_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT name,address FROM hotel_contexts WHERE request_id=? AND hotel_id=?",
                (request_id, hotel_id),
            ).fetchone()
        if row is None:
            raise SessionError("HOTEL_CONTEXT_MISSING")
        return {"name": row[0], "address": row[1]}

    def load(self, request_id, hotel_id, *, include_source=False):
        now = self.clock()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at,context_json,hotel_ids_json FROM search_sessions WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise SessionError("SEARCH_NOT_FOUND")
            if row[0] <= now:
                connection.execute("DELETE FROM search_sessions WHERE request_id=?", (request_id,))
                connection.commit()
                raise SessionError("SEARCH_EXPIRED")
            if hotel_id not in json.loads(row[2]):
                raise SessionError("HOTEL_NOT_IN_SEARCH")
            context = json.loads(row[1])
            if not include_source:
                context.pop("_supplier_source", None)
            return context

    def list_hotels(self, request_id):
        """Read only exact-name/address supplier members of a fresh search.

        This never pulls quotes, prices, keys, or raw provider responses.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at,context_json,hotel_ids_json FROM search_sessions "
                "WHERE request_id=?", (request_id,),
            ).fetchone()
            if row is None:
                raise SessionError("SEARCH_NOT_FOUND")
            if row[0] <= self.clock():
                raise SessionError("SEARCH_EXPIRED")
            members = set(json.loads(row[2]))
            rows = connection.execute(
                "SELECT hotel_id,name,address FROM hotel_contexts WHERE request_id=?",
                (request_id,),
            ).fetchall()
            return json.loads(row[1]), [
                {"id": hotel_id, "name": name, "address": address}
                for hotel_id, name, address in rows if hotel_id in members
            ]

    def replace_membership(self, request_id, hotel_ids, hotel_metadata):
        """Atomic membership update; neither context nor absolute expiration is renewed."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at FROM search_sessions WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise SessionError("SEARCH_NOT_FOUND")
            if row[0] <= self.clock():
                raise SessionError("SEARCH_EXPIRED")
            connection.execute(
                "UPDATE search_sessions SET hotel_ids_json=? WHERE request_id=?",
                (json.dumps(hotel_ids), request_id),
            )
            connection.execute("DELETE FROM hotel_contexts WHERE request_id=?", (request_id,))
            for hotel in hotel_metadata:
                if hotel["id"] in hotel_ids:
                    connection.execute(
                        "INSERT INTO hotel_contexts VALUES (?, ?, ?, ?)",
                        (request_id, hotel["id"], hotel["name"], hotel.get("address")),
                    )

    def ensure_live(self, request_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at FROM search_sessions WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise SessionError("SEARCH_NOT_FOUND")
            if row[0] <= self.clock():
                raise SessionError("SEARCH_EXPIRED")
            return row[0]
