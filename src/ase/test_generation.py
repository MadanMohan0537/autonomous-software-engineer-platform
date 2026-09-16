"""Model-backed test proposal contract; generated text is never executed directly."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ase.model import ModelClient, structured_completion


class TestProposal(BaseModel):
    path: str
    content: str
    behavior: str
    assumptions: list[str] = Field(default_factory=list)


class RegressionTestGenerator:
    def __init__(self, client: ModelClient) -> None:
        self.client = client

    async def propose(self, issue: str, patch: str, existing_tests: str) -> TestProposal:
        return await structured_completion(
            self.client,
            TestProposal,
            "Propose one regression test. Do not weaken, skip, or focus existing tests.",
            {"issue": issue, "patch": patch, "existing_tests": existing_tests},
        )
