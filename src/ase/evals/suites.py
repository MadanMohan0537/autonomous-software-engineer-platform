"""Evaluation task suites: a fixed set of tasks with the tests that define success.

A task is resolved when its fail-to-pass tests pass and its pass-to-pass tests still
pass, the SWE-bench definition. Suites are JSON files committed to the repository so a
number in a report can always be traced to the exact tasks that produced it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ase.contracts import Issue, Task, TaskSource


class EvalTask(BaseModel):
    id: str
    repository: str  # local path, or "owner/name" for remote-only metadata tasks
    base_sha: str | None = None
    title: str
    body: str = ""
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)
    test_patch: dict[str, str] = Field(
        default_factory=dict, description="test files (path -> content) that ship with the fix"
    )
    expected_files: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_task(self, number: int = 1) -> Task:
        """The agent's view of the task.

        When the acceptance tests ship with the fix (`test_patch`), they are hidden from the
        agent exactly as SWE-bench hides them: the agent works from the issue text and its
        own reproduction, and the hidden tests are applied only at grading time.
        """
        hidden = bool(self.test_patch)
        return Task(
            id=f"task_{self.id}",
            source=TaskSource.EVAL,
            issue=Issue(
                repository=self.repository, number=number, title=self.title, body=self.body
            ),
            fail_to_pass=[] if hidden else list(self.fail_to_pass),
            pass_to_pass=[] if hidden else list(self.pass_to_pass),
            base_sha=self.base_sha,
            metadata={
                "eval_task": self.id,
                "expected_files": self.expected_files,
                "hidden_tests": hidden,
                **self.metadata,
            },
        )


class Suite(BaseModel):
    name: str
    description: str = ""
    tasks: list[EvalTask] = Field(default_factory=list)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


def load_suite(path: Path) -> Suite:
    """Load a suite file, or a single legacy `benchmarks/tasks/*.json` task."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "tasks" in data:
        return Suite.model_validate(data)
    if isinstance(data, dict) and "issue" in data:
        issue = data["issue"]
        task = EvalTask(
            id=str(data.get("id", path.stem)),
            repository=str(data.get("repository", "")),
            title=str(issue.get("title", "")),
            body=str(issue.get("body", "")),
            expected_files=[str(item) for item in data.get("expected_files", [])],
            metadata={"required_checks": data.get("required_checks", [])},
        )
        return Suite(name=path.stem, tasks=[task])
    raise ValueError(f"unrecognised suite file: {path}")


def load_suites(directory: Path) -> list[Suite]:
    return [load_suite(path) for path in sorted(directory.glob("*.json"))]
