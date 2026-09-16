import json

import pytest

from ase.test_generation import RegressionTestGenerator


class FakeModel:
    async def complete(self, system: str, user: str) -> str:
        assert "regression test" in system
        assert json.loads(user)["issue"] == "bug"
        return json.dumps(
            {"path": "tests/test_bug.py", "content": "def test_bug(): pass", "behavior": "bug"}
        )


@pytest.mark.asyncio
async def test_generates_typed_test_proposal() -> None:
    proposal = await RegressionTestGenerator(FakeModel()).propose("bug", "patch", "tests")
    assert proposal.path == "tests/test_bug.py"
