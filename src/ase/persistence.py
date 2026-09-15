"""Durable SQLite stores for runs, tasks, and idempotent deliveries."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ase.contracts import AgentRun

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL,
  locked_at TEXT
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  delivery_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_queue_claim
  ON task_queue(status, available_at, id);
"""


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)


class SQLiteRunStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def save(self, run: AgentRun) -> None:
        payload = run.model_dump_json()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO agent_runs(id, state, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  state=excluded.state, payload=excluded.payload, updated_at=excluded.updated_at""",
                (
                    run.id,
                    run.state.value,
                    payload,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )

    def get(self, run_id: str) -> AgentRun | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return AgentRun.model_validate_json(row["payload"]) if row else None

    def list(self) -> list[AgentRun]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM agent_runs ORDER BY updated_at DESC"
            ).fetchall()
        return [AgentRun.model_validate_json(row["payload"]) for row in rows]


class TaskQueue:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def enqueue(self, run_id: str, kind: str, payload: str = "{}") -> int:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO task_queue(run_id, kind, payload, available_at) VALUES (?, ?, ?, ?)",
                (run_id, kind, payload, now),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("queue insert did not return an ID")
            return cursor.lastrowid

    def claim(self) -> sqlite3.Row | None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM task_queue
                WHERE status = 'pending' AND available_at <= ? ORDER BY id LIMIT 1""",
                (now,),
            ).fetchone()
            if row:
                connection.execute(
                    """UPDATE task_queue
                    SET status='running', attempts=attempts+1, locked_at=? WHERE id=?""",
                    (now, row["id"]),
                )
            return cast(sqlite3.Row | None, row)

    def complete(self, task_id: int, succeeded: bool) -> None:
        status = "completed" if succeeded else "failed"
        with self.database.connect() as connection:
            connection.execute("UPDATE task_queue SET status=? WHERE id=?", (status, task_id))

    def record_delivery(self, delivery_id: str, event_type: str) -> bool:
        try:
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO webhook_deliveries VALUES (?, ?, ?)",
                    (delivery_id, event_type, datetime.now(UTC).isoformat()),
                )
            return True
        except sqlite3.IntegrityError:
            return False
