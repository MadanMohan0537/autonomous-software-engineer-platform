"""Persistence: run store, trace store, and two implementations (memory and SQLite).

The trace store is the spine of the platform. Runs, steps, patches, test reports, reviews
and evaluation results are written here before any other side effect, and the evaluation
harness, the feedback loop and the reward scorer are all readers of these tables.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from ase.contracts import AgentRun, EvalResult, Patch, Review, Step, TestReport

RecordT = TypeVar("RecordT", bound=BaseModel)


class RunStore(Protocol):
    def save(self, run: AgentRun) -> None: ...
    def get(self, run_id: str) -> AgentRun | None: ...
    def list(self) -> builtins.list[AgentRun]: ...


class TraceStore(Protocol):
    def add_step(self, step: Step) -> None: ...
    def steps(self, run_id: str) -> builtins.list[Step]: ...
    def add_patch(self, patch: Patch) -> None: ...
    def patches(self, run_id: str) -> builtins.list[Patch]: ...
    def add_test_report(self, report: TestReport) -> None: ...
    def test_reports(self, run_id: str) -> builtins.list[TestReport]: ...
    def add_review(self, review: Review) -> None: ...
    def reviews(self, run_id: str | None = None) -> builtins.list[Review]: ...
    def add_eval_result(self, result: EvalResult) -> None: ...
    def eval_results(self, suite: str | None = None) -> builtins.list[EvalResult]: ...


class MemoryRunStore:
    """In-process store used by tests, the API default, and zero-credential development."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._steps: builtins.list[Step] = []
        self._patches: builtins.list[Patch] = []
        self._reports: builtins.list[TestReport] = []
        self._reviews: builtins.list[Review] = []
        self._results: builtins.list[EvalResult] = []

    def save(self, run: AgentRun) -> None:
        self._runs[run.id] = run.model_copy(deep=True)

    def get(self, run_id: str) -> AgentRun | None:
        run = self._runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    def list(self) -> builtins.list[AgentRun]:
        return [run.model_copy(deep=True) for run in self._runs.values()]

    def add_step(self, step: Step) -> None:
        self._steps.append(step)

    def steps(self, run_id: str) -> builtins.list[Step]:
        return [step for step in self._steps if step.run_id == run_id]

    def add_patch(self, patch: Patch) -> None:
        self._patches.append(patch)

    def patches(self, run_id: str) -> builtins.list[Patch]:
        return [patch for patch in self._patches if patch.run_id == run_id]

    def add_test_report(self, report: TestReport) -> None:
        self._reports.append(report)

    def test_reports(self, run_id: str) -> builtins.list[TestReport]:
        return [report for report in self._reports if report.run_id == run_id]

    def add_review(self, review: Review) -> None:
        self._reviews.append(review)

    def reviews(self, run_id: str | None = None) -> builtins.list[Review]:
        return [item for item in self._reviews if run_id is None or item.run_id == run_id]

    def add_eval_result(self, result: EvalResult) -> None:
        self._results.append(result)

    def eval_results(self, suite: str | None = None) -> builtins.list[EvalResult]:
        return [item for item in self._results if suite is None or item.suite == suite]


_TABLES = {
    "runs": "id TEXT PRIMARY KEY, state TEXT, updated_at TEXT, body TEXT NOT NULL",
    "steps": "run_id TEXT, idx INTEGER, body TEXT NOT NULL, PRIMARY KEY (run_id, idx)",
    "patches": "run_id TEXT, idx INTEGER, body TEXT NOT NULL",
    "test_reports": "run_id TEXT, idx INTEGER, body TEXT NOT NULL",
    "reviews": "run_id TEXT, pr_number INTEGER, body TEXT NOT NULL",
    "eval_results": "suite TEXT, task_id TEXT, config_name TEXT, run_id TEXT, body TEXT NOT NULL",
}


class SqliteRunStore:
    """Single-file store. Zero operations, and every table exports to JSONL for git."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        for name, columns in _TABLES.items():
            self.connection.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")
        self.connection.commit()

    # -- runs -------------------------------------------------------------------------
    def save(self, run: AgentRun) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO runs (id, state, updated_at, body) VALUES (?, ?, ?, ?)",
            (run.id, run.state.value, run.updated_at.isoformat(), run.model_dump_json()),
        )
        self.connection.commit()

    def get(self, run_id: str) -> AgentRun | None:
        row = self.connection.execute("SELECT body FROM runs WHERE id = ?", (run_id,)).fetchone()
        return AgentRun.model_validate_json(row[0]) if row else None

    def list(self) -> builtins.list[AgentRun]:
        rows = self.connection.execute("SELECT body FROM runs ORDER BY updated_at").fetchall()
        return [AgentRun.model_validate_json(row[0]) for row in rows]

    # -- traces -----------------------------------------------------------------------
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

    # -- export -----------------------------------------------------------------------
    def export_jsonl(self, table: str, destination: Path) -> int:
        if table not in _TABLES:
            raise KeyError(table)
        rows = self.connection.execute(f"SELECT body FROM {table} ORDER BY rowid").fetchall()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(json.loads(row[0]), sort_keys=True) + "\n")
        return len(rows)

    def close(self) -> None:
        self.connection.close()

    # -- helpers ----------------------------------------------------------------------
    def _insert(
        self, table: str, columns: Sequence[str], values: Iterable[object], record: BaseModel
    ) -> None:
        names = ", ".join([*columns, "body"])
        marks = ", ".join("?" for _ in [*columns, "body"])
        self.connection.execute(
            f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})",
            (*values, record.model_dump_json()),
        )
        self.connection.commit()

    def _select(
        self,
        model: type[RecordT],
        table: str,
        where: str,
        params: tuple[object, ...],
        order: str,
    ) -> builtins.list[RecordT]:
        rows = self.connection.execute(
            f"SELECT body FROM {table} WHERE {where} ORDER BY {order}", params
        ).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]
