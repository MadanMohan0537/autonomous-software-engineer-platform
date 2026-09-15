"""Persistence interfaces and an in-memory reference implementation."""

from __future__ import annotations

from typing import Protocol

from ase.contracts import AgentRun


class RunStore(Protocol):
    def save(self, run: AgentRun) -> None: ...
    def get(self, run_id: str) -> AgentRun | None: ...
    def list(self) -> list[AgentRun]: ...


class MemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}

    def save(self, run: AgentRun) -> None:
        self._runs[run.id] = run.model_copy(deep=True)

    def get(self, run_id: str) -> AgentRun | None:
        run = self._runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    def list(self) -> list[AgentRun]:
        return [run.model_copy(deep=True) for run in self._runs.values()]
