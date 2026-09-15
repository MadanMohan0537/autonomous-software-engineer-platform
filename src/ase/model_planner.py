"""Model-backed planning that returns the same governed ChangePlan contract."""

from __future__ import annotations

from ase.contracts import ChangePlan, ContextItem, Issue
from ase.model import ModelClient, structured_completion


class ModelPlanner:
    def __init__(self, client: ModelClient) -> None:
        self.client = client

    async def plan(self, issue: Issue, context: list[ContextItem]) -> ChangePlan:
        return await structured_completion(
            self.client,
            ChangePlan,
            "Propose the smallest reviewable engineering plan. Do not claim execution.",
            {
                "issue": issue.model_dump(),
                "context": [item.model_dump() for item in context],
            },
        )
