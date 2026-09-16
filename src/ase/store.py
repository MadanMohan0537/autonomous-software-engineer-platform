"""Store protocols, the in-memory store, and the SQLite store opened by path.

The trace store is the spine of the platform. Runs, steps, patches, test reports, reviews
and evaluation results are written here before any other side effect, and the evaluation
harness, the feedback loop and the reward scorer are all readers of these tables. The
SQLite implementation lives in `ase.persistence` next to the worker queue.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Protocol

from ase.contracts import AgentRun, EvalResult, Patch, Review, Step, TestReport
from ase.persistence import SQLiteDatabase, SQLiteRunStore


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


class PlatformStore(RunStore, TraceStore, Protocol):
    """Runs plus traces: what the agent, the evaluation harness and the feedback loop need."""


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


class SqliteRunStore(SQLiteRunStore):
    """`SQLiteRunStore` opened from a path: runs and traces in one file, JSONL export."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        super().__init__(SQLiteDatabase(self.path))

    def close(self) -> None:  # connections are per call; kept for API symmetry
        return None
