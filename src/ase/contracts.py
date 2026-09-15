"""Typed contracts shared by every platform module."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


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


class AgentRun(BaseModel):
    id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    issue: Issue
    state: RunState = RunState.INGEST_ISSUE
    workspace: Path | None = None
    context: list[ContextItem] = Field(default_factory=list)
    plan: ChangePlan | None = None
    evaluation: EvaluationReport | None = None
    pr_url: str | None = None
    events: list[RunEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"arbitrary_types_allowed": True}

    def record(self, kind: str, **payload: Any) -> None:
        self.events.append(
            RunEvent(sequence=len(self.events) + 1, state=self.state, kind=kind, payload=payload)
        )
        self.updated_at = utc_now()
