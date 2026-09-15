"""Classify a red build and decide what happens next.

Rules first, model second: the cheap deterministic classifier handles the common shapes
(lint, a failing test, a missing module, a network blip) and only an unrecognised log is
sent to the small model. Routing is deterministic and fails closed: anything unknown is
escalated to a human, never retried blindly and never turned into an agent task.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ase.ci.actions import FailureLog
from ase.contracts import Issue, Task, TaskSource
from ase.llm.client import LLMClient, Message

TRIAGE_SYSTEM = """You classify a failed CI job from its log excerpt.
Reply with JSON only: {"kind": "flake" | "lint" | "test_regression" | "env" | "unknown",
"reason": "one sentence", "confidence": 0.0-1.0}. Use "flake" only for transient
infrastructure problems (network, rate limits, runner outages), never for failing tests."""


class FailureKind(StrEnum):
    FLAKE = "flake"
    LINT = "lint"
    TEST_REGRESSION = "test_regression"
    ENV = "env"
    UNKNOWN = "unknown"


class Classification(BaseModel):
    kind: FailureKind
    reason: str
    confidence: float = Field(ge=0, le=1)
    source: str  # "rules" | "model"


class Action(BaseModel):
    name: str  # "retry" | "reenter" | "escalate"
    reason: str
    task: Task | None = None


_RULES: list[tuple[FailureKind, re.Pattern[str], str]] = [
    (
        FailureKind.FLAKE,
        re.compile(
            r"(ETIMEDOUT|ECONNRESET|Connection reset|rate limit|503 Service|502 Bad Gateway|"
            r"The hosted runner .* lost communication|Could not resolve host|"
            r"TLS handshake timeout)",
            re.I,
        ),
        "transient network or runner failure",
    ),
    (
        FailureKind.ENV,
        re.compile(
            r"(ModuleNotFoundError|No matching distribution found|ResolutionImpossible|"
            r"command not found|No module named|npm ERR! 404|Unable to locate package)",
            re.I,
        ),
        "dependency or environment problem",
    ),
    (
        FailureKind.LINT,
        re.compile(
            r"(ruff|mypy|flake8|eslint|\bE\d{3}\b|\bF\d{3}\b|error: Incompatible|would reformat|"
            r"Found \d+ error[s]? in \d+ file)",
            re.I,
        ),
        "lint, formatting or type check failed",
    ),
    (
        FailureKind.TEST_REGRESSION,
        re.compile(
            r"(FAILED tests?/|AssertionError|\d+ failed|Traceback \(most recent call last\))"
        ),
        "tests failed",
    ),
]


def classify_by_rules(excerpt: str) -> Classification | None:
    for kind, pattern, reason in _RULES:
        if pattern.search(excerpt):
            return Classification(kind=kind, reason=reason, confidence=0.7, source="rules")
    return None


class Triage:
    def __init__(
        self, llm: LLMClient | None = None, model: str = "claude-haiku-4-5-20251001"
    ) -> None:
        self.llm = llm
        self.model = model

    def classify(self, log: FailureLog) -> Classification:
        by_rules = classify_by_rules(log.excerpt)
        if by_rules is not None:
            return by_rules
        if self.llm is None:
            return Classification(
                kind=FailureKind.UNKNOWN, reason="no rule matched", confidence=0.0, source="rules"
            )
        completion = self.llm.complete(
            model=self.model,
            system=TRIAGE_SYSTEM,
            messages=[
                Message.user(
                    f"Job: {log.job.name} / {log.job.failed_step}\n\n{log.excerpt[-6000:]}"
                )
            ],
            max_tokens=300,
        )
        payload = _extract_json(completion.text)
        try:
            kind = FailureKind(str(payload.get("kind", "unknown")).lower())
        except ValueError:
            kind = FailureKind.UNKNOWN
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        return Classification(
            kind=kind,
            reason=str(payload.get("reason") or "model classification"),
            confidence=max(0.0, min(1.0, confidence)),
            source="model",
        )

    @staticmethod
    def route(
        classification: Classification,
        log: FailureLog,
        repository: str,
        pr_number: int | None,
        retries_so_far: int = 0,
    ) -> Action:
        kind = classification.kind
        if kind == FailureKind.FLAKE and retries_so_far < 1:
            return Action(name="retry", reason=classification.reason)
        if kind in {FailureKind.LINT, FailureKind.TEST_REGRESSION}:
            issue = Issue(
                repository=repository,
                number=pr_number or 1,
                title=f"CI failed: {log.job.name} ({log.job.failed_step or 'unknown step'})",
                body=(
                    f"Workflow `{log.run.name}` failed ({classification.kind.value}: "
                    f"{classification.reason}).\nRun: {log.run.html_url}\n\n"
                    f"Log excerpt:\n```\n{log.excerpt[-4000:]}\n```"
                ),
                labels=["ci", classification.kind.value],
            )
            task = Task(
                source=TaskSource.CI,
                issue=issue,
                base_sha=log.run.head_sha or None,
                metadata={"workflow_run": log.run.id, "job": log.job.id, "kind": kind.value},
            )
            return Action(name="reenter", reason=classification.reason, task=task)
        return Action(name="escalate", reason=classification.reason)


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
