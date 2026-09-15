"""Grade a patch the SWE-bench way: apply it to a clean checkout and run the tests.

The agent's own test run is never trusted for the score. Grading starts from the base
commit in a fresh worktree, applies the patch, installs the task's test files, and runs
the fail-to-pass and pass-to-pass tests in a sandbox.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from ase.evals.suites import EvalTask
from ase.sandbox import Sandbox
from ase.testrun import PytestRunner
from ase.workspace import WorkspaceManager

SandboxFactory = Callable[[Path], Sandbox]


class GradeResult(BaseModel):
    task_id: str
    resolved: bool
    applied: bool
    fail_to_pass: dict[str, bool] = Field(default_factory=dict)
    pass_to_pass: dict[str, bool] = Field(default_factory=dict)
    expected_file_overlap: float | None = None
    notes: list[str] = Field(default_factory=list)


def apply_patch(root: Path, diff: str) -> bool:
    if not diff.strip():
        return False
    completed = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=root,
        input=diff,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return completed.returncode == 0


class Grader:
    def __init__(
        self,
        workspaces: WorkspaceManager,
        sandbox_factory: SandboxFactory,
        python: str | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.sandbox_factory = sandbox_factory
        self.python = python

    def grade(self, task: EvalTask, diff: str, patch_files: list[str] | None = None) -> GradeResult:
        result = GradeResult(task_id=task.id, resolved=False, applied=False)
        if task.expected_files and patch_files is not None:
            overlap = len(set(task.expected_files) & set(patch_files)) / len(task.expected_files)
            result.expected_file_overlap = round(overlap, 4)
        repository = Path(task.repository)
        if not repository.is_dir():
            result.notes.append("repository is not a local path; only metadata grading is possible")
            return result
        workspace = self.workspaces.create(repository, task.base_sha)
        try:
            result.applied = apply_patch(workspace.path, diff)
            if not result.applied:
                result.notes.append("patch did not apply cleanly to the base commit")
                return result
            for path, content in task.test_patch.items():
                target = workspace.path / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            if not task.fail_to_pass and not task.pass_to_pass:
                result.notes.append("task has no acceptance tests; running the whole suite")
            report = PytestRunner(self.sandbox_factory(workspace.path), self.python).report(
                f"grade-{task.id}", 0, task.fail_to_pass, task.pass_to_pass
            )
            result.fail_to_pass = report.fail_to_pass
            result.pass_to_pass = report.pass_to_pass
            result.resolved = report.green
            if not report.green:
                result.notes.append(
                    f"{report.failed} failed, {report.errors} errors; regressions: "
                    f"{', '.join(report.regressions) or 'none'}"
                )
        finally:
            self.workspaces.remove(workspace)
        return result
