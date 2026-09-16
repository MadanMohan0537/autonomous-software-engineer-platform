"""Durable SQLite stores for runs, traces, tasks, and idempotent deliveries.

One database file holds everything the platform records: agent runs, the trace tables
that are the spine of evaluation and feedback (steps, patches, test reports, reviews,
evaluation results), the worker task queue, and webhook delivery ids. The schema is
mirrored in `migrations/` for anyone applying it outside the application.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar, cast

from pydantic import BaseModel

from ase.contracts import AgentRun, EvalResult, Patch, Review, Step, TestReport

RecordT = TypeVar("RecordT", bound=BaseModel)

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
CREATE TABLE IF NOT EXISTS steps (
  run_id TEXT NOT NULL, idx INTEGER NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS patches (run_id TEXT NOT NULL, idx INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_reports (
  run_id TEXT NOT NULL, idx INTEGER NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (run_id TEXT NOT NULL, pr_number INTEGER, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eval_results (
  suite TEXT NOT NULL, task_id TEXT NOT NULL, config_name TEXT NOT NULL,
  run_id TEXT NOT NULL, body TEXT NOT NULL
);
"""

TRACE_TABLES = ("agent_runs", "steps", "patches", "test_reports", "reviews", "eval_results")


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
    """Runs plus traces on one database: implements `ase.store.PlatformStore`."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    # -- runs ---------------------------------------------------------------------------
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

    def list(self) -> builtins.list[AgentRun]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM agent_runs ORDER BY updated_at DESC"
            ).fetchall()
        return [AgentRun.model_validate_json(row["payload"]) for row in rows]

    # -- traces -------------------------------------------------------------------------
    def add_step(self, step: Step) -> None:
        self._insert("steps", ("run_id", "idx"), (step.run_id, step.index), step)

    def steps(self, run_id: str) -> builtins.list[Step]:
        return self._select(Step, "steps", "run_id = ?", (run_id,), order="idx")

    def add_patch(self, patch: Patch) -> None:
        self._insert("patches", ("run_id", "idx"), (patch.run_id, patch.step_index), patch)

    def patches(self, run_id: str) -> builtins.list[Patch]:
        return self._select(Patch, "patches", "run_id = ?", (run_id,), order="idx")

    def add_test_report(self, report: TestReport) -> None:
        self._insert("test_reports", ("run_id", "idx"), (report.run_id, report.step_index), report)

    def test_reports(self, run_id: str) -> builtins.list[TestReport]:
        return self._select(TestReport, "test_reports", "run_id = ?", (run_id,), order="idx")

    def add_review(self, review: Review) -> None:
        self._insert("reviews", ("run_id", "pr_number"), (review.run_id, review.pr_number), review)

    def reviews(self, run_id: str | None = None) -> builtins.list[Review]:
        if run_id is None:
            return self._select(Review, "reviews", "1 = 1", (), order="rowid")
        return self._select(Review, "reviews", "run_id = ?", (run_id,), order="rowid")

    def add_eval_result(self, result: EvalResult) -> None:
        self._insert(
            "eval_results",
            ("suite", "task_id", "config_name", "run_id"),
            (result.suite, result.task_id, result.config_name, result.run_id),
            result,
        )

    def eval_results(self, suite: str | None = None) -> builtins.list[EvalResult]:
        if suite is None:
            return self._select(EvalResult, "eval_results", "1 = 1", (), order="rowid")
        return self._select(EvalResult, "eval_results", "suite = ?", (suite,), order="rowid")

    # -- export -------------------------------------------------------------------------
    def export_jsonl(self, table: str, destination: Path) -> int:
        if table not in TRACE_TABLES:
            raise KeyError(table)
        column = "payload" if table == "agent_runs" else "body"
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT {column} FROM {table} ORDER BY rowid").fetchall()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(json.loads(row[0]), sort_keys=True) + "\n")
        return len(rows)

    # -- helpers ------------------------------------------------------------------------
    def _insert(
        self, table: str, columns: Sequence[str], values: Iterable[object], record: BaseModel
    ) -> None:
        names = ", ".join([*columns, "body"])
        marks = ", ".join("?" for _ in [*columns, "body"])
        with self.database.connect() as connection:
            connection.execute(
                f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})",
                (*values, record.model_dump_json()),
            )

    def _select(
        self,
        model: type[RecordT],
        table: str,
        where: str,
        params: tuple[object, ...],
        order: str,
    ) -> builtins.list[RecordT]:
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT body FROM {table} WHERE {where} ORDER BY {order}", params
            ).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]


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
