"""Replaceable planning boundary with a deterministic baseline."""

from __future__ import annotations

from typing import Protocol

from ase.contracts import ChangePlan, ContextItem, Issue, PlanStep


class Planner(Protocol):
    async def plan(self, issue: Issue, context: list[ContextItem]) -> ChangePlan: ...


class DeterministicPlanner:
    """Safe baseline used for tests and zero-credential development."""

    async def plan(self, issue: Issue, context: list[ContextItem]) -> ChangePlan:
        files = [item.path for item in context[:5]]
        return ChangePlan(
            summary=f"Investigate and resolve issue #{issue.number}: {issue.title}",
            steps=[
                PlanStep(description="Reproduce the reported behavior", files=files, risk="low"),
                PlanStep(
                    description="Implement the smallest supported change",
                    files=files,
                    risk="medium",
                ),
                PlanStep(
                    description="Add regression coverage and run verification",
                    files=files,
                    risk="low",
                ),
            ],
            assumptions=["The issue is reproducible in the configured workspace."],
        )
