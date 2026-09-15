"""The typed state the workflow carries between nodes.

Everything in it is JSON-serialisable so LangGraph can checkpoint it after every node and
a crashed run can resume where it stopped. Domain models travel as their `model_dump`
and are rebuilt by the node that needs them.
"""

from __future__ import annotations

from typing import Any, TypedDict

from ase.contracts import ChangePlan, ContextItem, Patch, RunConfig, Task, TestReport


class AgentState(TypedDict, total=False):
    run_id: str
    repository: str  # local path of the source repository
    remote: str | None  # "owner/name" on GitHub when delivery is configured
    task: dict[str, Any]
    config: dict[str, Any]
    base_sha: str
    workspace: str | None
    context: list[dict[str, Any]]
    repo_map: str
    plan: dict[str, Any] | None
    reproduction: dict[str, str] | None
    installed_tests: dict[str, str]  # platform-written test files -> content digest
    plan_decision: dict[str, Any] | None
    pr_decision: dict[str, Any] | None
    patch: dict[str, Any] | None
    patch_hashes: list[str]
    report: dict[str, Any] | None
    iteration: int
    focus: str
    decision: str | None
    outcome: str | None
    outcome_reason: str
    pr_url: str | None
    pr_number: int | None
    started_at: float
    notes: list[str]


def initial_state(
    run_id: str,
    repository: str,
    task: Task,
    config: RunConfig,
    remote: str | None = None,
) -> AgentState:
    return AgentState(
        run_id=run_id,
        repository=repository,
        remote=remote,
        task=task.model_dump(mode="json"),
        config=config.model_dump(mode="json"),
        base_sha="",
        workspace=None,
        context=[],
        repo_map="",
        plan=None,
        reproduction=None,
        installed_tests={},
        plan_decision=None,
        pr_decision=None,
        patch=None,
        patch_hashes=[],
        report=None,
        iteration=0,
        focus="",
        decision=None,
        outcome=None,
        outcome_reason="",
        pr_url=None,
        pr_number=None,
        started_at=0.0,
        notes=[],
    )


def task_of(state: AgentState) -> Task:
    return Task.model_validate(state["task"])


def config_of(state: AgentState) -> RunConfig:
    return RunConfig.model_validate(state["config"])


def plan_of(state: AgentState) -> ChangePlan | None:
    plan = state.get("plan")
    return ChangePlan.model_validate(plan) if plan else None


def context_of(state: AgentState) -> list[ContextItem]:
    return [ContextItem.model_validate(item) for item in state.get("context", [])]


def report_of(state: AgentState) -> TestReport | None:
    report = state.get("report")
    return TestReport.model_validate(report) if report else None


def patch_of(state: AgentState) -> Patch | None:
    patch = state.get("patch")
    return Patch.model_validate(patch) if patch else None
