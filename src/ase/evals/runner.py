"""Run the agent over a suite under a named configuration and grade every result."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ase.agent.runner import ApprovalGate, AutoApprove
from ase.agent.service import AgentService
from ase.agent.state import patch_of
from ase.contracts import EvalResult, RunConfig
from ase.evals.grade import Grader
from ase.evals.report import write_results
from ase.evals.suites import Suite


class EvalRunner:
    def __init__(
        self,
        service: AgentService,
        grader: Grader,
        results_dir: Path,
        gate: ApprovalGate | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self.grader = grader
        self.results_dir = results_dir
        self.gate = gate or AutoApprove()
        self._clock = clock

    def run_suite(self, suite: Suite, config: RunConfig) -> list[EvalResult]:
        results: list[EvalResult] = []
        for number, eval_task in enumerate(suite.tasks, start=1):
            task = eval_task.to_task(number)
            run, state = self.service.create_run(task.issue, config=config, task=task)
            started = self._clock()
            final = self.service.run_sequential(state, self.gate)
            wall = self._clock() - started
            patch = patch_of(final)
            grade = self.grader.grade(
                eval_task, patch.diff if patch else "", patch.files if patch else []
            )
            finished = self.service.store.get(run.id) or run
            notes = [final.get("outcome") or "unknown", *grade.notes]
            if grade.expected_file_overlap is not None:
                notes.append(f"expected-file overlap {grade.expected_file_overlap:.0%}")
            result = EvalResult(
                suite=suite.name,
                task_id=eval_task.id,
                run_id=run.id,
                config_name=config.name,
                resolved=grade.resolved,
                cost_usd=finished.cost_usd,
                steps=finished.steps,
                wall_s=round(wall, 2),
                notes="; ".join(notes),
            )
            self.service.store.add_eval_result(result)
            results.append(result)
        write_results(results, self.results_dir, suite.name, config.name)
        return results
