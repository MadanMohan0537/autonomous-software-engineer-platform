"""Mutation-test execution and score parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ase.sandbox import LocalSandbox


@dataclass(frozen=True)
class MutationReport:
    killed: int
    survived: int
    timeout: int
    suspicious: int

    @property
    def score(self) -> float:
        total = self.killed + self.survived + self.timeout + self.suspicious
        return self.killed / total if total else 0.0


class MutationRunner:
    def __init__(self, repository: Path) -> None:
        self.sandbox = LocalSandbox(repository)

    def run_mutmut(self) -> MutationReport:
        run = self.sandbox.run(["python", "-m", "mutmut", "run"], timeout=120)
        if run.exit_code not in {0, 1}:
            raise RuntimeError(run.stderr or "mutation run failed")
        results = self.sandbox.run(["python", "-m", "mutmut", "results", "--json"])
        if results.exit_code:
            raise RuntimeError(results.stderr or "mutation results failed")
        return self.parse(results.stdout)

    @staticmethod
    def parse(payload: str) -> MutationReport:
        data = json.loads(payload)
        return MutationReport(
            killed=int(data.get("killed", 0)),
            survived=int(data.get("survived", 0)),
            timeout=int(data.get("timeout", 0)),
            suspicious=int(data.get("suspicious", 0)),
        )
