"""SQLite persistence: conversation history, per-turn traces and support tickets.

SQLite is enough for a single-store prototype (zero setup, one file, ACID). The
schema maps 1:1 to Postgres if this ever runs with several workers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    customer_id  INTEGER,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    user_text    TEXT NOT NULL,
    reply_text   TEXT NOT NULL,
    tool_calls   TEXT NOT NULL,
    usage        TEXT NOT NULL,
    model        TEXT NOT NULL,
    latency_ms   INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol     TEXT UNIQUE,
    session_id   TEXT,
    category     TEXT NOT NULL,
    summary      TEXT NOT NULL,
    order_id     INTEGER,
    customer_id  INTEGER,
    contact      TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""


class Storage:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the API's worker threads, serialised by a lock.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    # -------------------------------------------------------------- sessions
    def get_session(self, session_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def create_session(self, session_id: str, provider: str, customer_id: int | None, now: datetime) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO sessions (id, provider, customer_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, provider, customer_id, now.isoformat(), now.isoformat()),
            )

    # -------------------------------------------------------------- messages
    def load_messages(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT payload FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def append_turn(self, session_id: str, messages: list[dict], trace: dict, now: datetime) -> None:
        """Append a whole turn atomically: history is append-only, never rewritten."""
        stamp = now.isoformat()
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT INTO messages (session_id, payload, created_at) VALUES (?, ?, ?)",
                [(session_id, json.dumps(m, ensure_ascii=False), stamp) for m in messages],
            )
            self.conn.execute(
                "INSERT INTO turns (session_id, user_text, reply_text, tool_calls, usage, model, "
                "latency_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    trace["user_text"],
                    trace["reply_text"],
                    json.dumps(trace["tool_calls"], ensure_ascii=False),
                    json.dumps(trace["usage"]),
                    trace["model"],
                    trace["latency_ms"],
                    stamp,
                ),
            )
            self.conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (stamp, session_id))

    # -------------------------------------------------------------- tickets
    def create_ticket(
        self,
        *,
        session_id: str,
        category: str,
        summary: str,
        order_id: int | None,
        customer_id: int | None,
        contact: str | None,
        now: datetime,
    ) -> str:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "INSERT INTO tickets (session_id, category, summary, order_id, customer_id, contact, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, category, summary, order_id, customer_id, contact, now.isoformat()),
            )
            protocol = f"EM-{now:%Y%m%d}-{cursor.lastrowid:04d}"
            self.conn.execute("UPDATE tickets SET protocol = ? WHERE id = ?", (protocol, cursor.lastrowid))
        return protocol

    def tickets(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
