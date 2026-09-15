"""Typed contracts shared by every platform module."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class RunState(StrEnum):
    INGEST_ISSUE = "ingest_issue"
    REPRODUCE_FAILURE = "reproduce_failure"
    RETRIEVE_CONTEXT = "retrieve_context"
    PROPOSE_PLAN = "propose_plan"
    AWAIT_PLAN_APPROVAL = "await_plan_approval"
    CREATE_WORKSPACE = "create_workspace"
    IMPLEMENT_PATCH = "implement_patch"
    GENERATE_TESTS = "generate_tests"
    VERIFY_PATCH = "verify_patch"
    AWAIT_PR_APPROVAL = "await_pr_approval"
    OPEN_DRAFT_PR = "open_draft_pr"
    CAPTURE_REVIEW = "capture_review"
    COMPLETED = "completed"
    FAILED = "failed"


class Decision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Issue(BaseModel):
    repository: str
    number: int = Field(ge=1)
    title: str = Field(min_length=1)
    body: str = ""
    labels: list[str] = Field(default_factory=list)


class Symbol(BaseModel):
    id: str
    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    language: str


class Relationship(BaseModel):
    source: str
    target: str
    kind: str


class ContextItem(BaseModel):
    path: str
    reason: str
    score: float = Field(ge=0, le=1)
    symbols: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    description: str
    files: list[str] = Field(default_factory=list)
    risk: str = "low"


class ChangePlan(BaseModel):
    summary: str
    steps: list[PlanStep]
    assumptions: list[str] = Field(default_factory=list)
    approval: Decision = Decision.PENDING


class CommandResult(BaseModel):
    command: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


class CheckResult(BaseModel):
    name: str
    passed: bool
    details: str = ""
    command: CommandResult | None = None


class EvaluationReport(BaseModel):
    passed: bool
    checks: list[CheckResult]
    mutation_score: float | None = None
    changed_files: list[str] = Field(default_factory=list)
    policy_violations: list[str] = Field(default_factory=list)


class RunEvent(BaseModel):
    sequence: int
    state: RunState
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TaskSource(StrEnum):
    """Where a unit of work came from. Machine-created tasks are never confused with human ones."""

    ISSUE = "issue"
    CI = "ci"
    MUTANT = "mutant"
    EVAL = "eval"
    MANUAL = "manual"


class Task(BaseModel):
    """A unit of agent work with provenance and, when known, the tests that define success."""

    id: str = Field(default_factory=lambda: new_id("task"))
    source: TaskSource = TaskSource.ISSUE
    issue: Issue
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)
    base_sha: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Budget(BaseModel):
    """Hard limits that the workflow checks at every edge rather than hoping for."""

    max_iterations: int = Field(default=6, ge=1)
    max_tokens: int = Field(default=400_000, ge=1)
    max_usd: float = Field(default=3.0, gt=0)
    max_seconds: int = Field(default=900, ge=1)
    max_tool_calls_per_iteration: int = Field(default=25, ge=1)


ToolMode = Literal["structured", "command_only"]


class RunConfig(BaseModel):
    """A named configuration so evaluation tables can compare like with like."""

    name: str = "default"
    planner_model: str = "claude-opus-5"
    coder_model: str = "claude-sonnet-5"
    classifier_model: str = "claude-haiku-4-5-20251001"
    tool_mode: ToolMode = "structured"
    use_index: bool = True
    candidates: int = Field(default=1, ge=1)
    budget: Budget = Field(default_factory=Budget)
    seed: int = 0


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    exit_code: int | None = None
    duration_ms: int = 0


class Step(BaseModel):
    """One model turn inside a run. Every step is written before any other side effect."""

    run_id: str
    index: int
    node: str
    model: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: list[ToolCall] = Field(default_factory=list)
    thought: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class Patch(BaseModel):
    run_id: str
    step_index: int
    diff: str
    files: list[str] = Field(default_factory=list)
    touched_tests: bool = False
    sha256: str = ""

    @classmethod
    def from_diff(cls, run_id: str, step_index: int, diff: str) -> Patch:
        files = sorted(
            {
                line[6:].strip()
                for line in diff.splitlines()
                if line.startswith("+++ b/") or line.startswith("--- a/")
            }
        )
        return cls(
            run_id=run_id,
            step_index=step_index,
            diff=diff,
            files=files,
            touched_tests=any(looks_like_test(path) for path in files),
            sha256=hashlib.sha256(diff.encode()).hexdigest(),
        )


def looks_like_test(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name
    return (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


class TestReport(BaseModel):
    """The only ground truth: fail-to-pass tests now pass and pass-to-pass tests still pass."""

    __test__: ClassVar[bool] = False  # not a pytest test class

    run_id: str
    step_index: int = 0
    fail_to_pass: dict[str, bool] = Field(default_factory=dict)
    pass_to_pass: dict[str, bool] = Field(default_factory=dict)
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    coverage_pct: float | None = None
    mutation_score: float | None = None
    stdout_tail: str = ""

    @property
    def green(self) -> bool:
        return (
            self.failed == 0
            and self.errors == 0
            and all(self.fail_to_pass.values())
            and all(self.pass_to_pass.values())
        )

    @property
    def regressions(self) -> list[str]:
        return sorted(name for name, ok in self.pass_to_pass.items() if not ok)


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"
    MERGED = "merged"
    CLOSED = "closed"


class Review(BaseModel):
    """A human decision on a draft pull request, mirrored from GitHub. Every review is a label."""

    run_id: str
    pr_number: int
    decision: ReviewDecision
    reviewer: str = ""
    comments: list[str] = Field(default_factory=list)
    at: datetime = Field(default_factory=utc_now)


class EvalResult(BaseModel):
    suite: str
    task_id: str
    run_id: str
    config_name: str
    resolved: bool
    cost_usd: float = 0.0
    steps: int = 0
    wall_s: float = 0.0
    notes: str = ""


class AgentRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    issue: Issue
    state: RunState = RunState.INGEST_ISSUE
    task: Task | None = None
    config: RunConfig = Field(default_factory=RunConfig)
    base_sha: str | None = None
    workspace: Path | None = None
    context: list[ContextItem] = Field(default_factory=list)
    plan: ChangePlan | None = None
    iteration: int = 0
    steps: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    patch: Patch | None = None
    test_report: TestReport | None = None
    evaluation: EvaluationReport | None = None
    outcome: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    events: list[RunEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"arbitrary_types_allowed": True}

    def record(self, kind: str, **payload: Any) -> None:
        self.events.append(
            RunEvent(sequence=len(self.events) + 1, state=self.state, kind=kind, payload=payload)
        )
        self.updated_at = utc_now()

    def add_step(self, step: Step) -> None:
        self.steps += 1
        self.prompt_tokens += step.prompt_tokens
        self.output_tokens += step.output_tokens
        self.cost_usd = round(self.cost_usd + step.cost_usd, 6)
        self.updated_at = utc_now()

    def budget_exceeded(self, elapsed_seconds: float) -> str | None:
        """Return the first exhausted budget, or None. Checked at every edge of the workflow."""
        budget = self.config.budget
        if self.iteration >= budget.max_iterations:
            return f"iterations {self.iteration} >= {budget.max_iterations}"
        if self.prompt_tokens + self.output_tokens >= budget.max_tokens:
            return f"tokens {self.prompt_tokens + self.output_tokens} >= {budget.max_tokens}"
        if self.cost_usd >= budget.max_usd:
            return f"cost ${self.cost_usd:.2f} >= ${budget.max_usd:.2f}"
        if elapsed_seconds >= budget.max_seconds:
            return f"elapsed {elapsed_seconds:.0f}s >= {budget.max_seconds}s"
        return None
