import json

import pytest

from ase.contracts import ChangePlan
from ase.model import structured_completion
from ase.model_planner import ModelPlanner


class FakeModel:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, system: str, user: str) -> str:
        assert "JSON" in system
        assert json.loads(user)
        return self.response


@pytest.mark.asyncio
async def test_model_planner_validates_contract() -> None:
    response = json.dumps({"summary": "Fix", "steps": [{"description": "Test", "files": []}]})
    plan = await ModelPlanner(FakeModel(response)).plan(
        __import__("ase.contracts", fromlist=["Issue"]).Issue(
            repository="demo", number=1, title="Bug"
        ),
        [],
    )
    assert isinstance(plan, ChangePlan)


@pytest.mark.asyncio
async def test_structured_completion_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        await structured_completion(FakeModel("not-json"), ChangePlan, "Plan", {"x": 1})
