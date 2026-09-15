from pathlib import Path

import pytest

from ase.contracts import Decision, Issue, RunState
from ase.orchestrator import InvalidTransition, Orchestrator


@pytest.mark.asyncio
async def test_analysis_stops_at_human_approval(tmp_path: Path) -> None:
    (tmp_path / "payments.py").write_text("def refund():\n    return False\n", encoding="utf-8")
    orchestrator = Orchestrator()
    run = orchestrator.create(Issue(repository="demo", number=7, title="Refund fails"))
    run = await orchestrator.analyze(run.id, tmp_path)
    assert run.state == RunState.AWAIT_PLAN_APPROVAL
    assert run.context[0].path == "payments.py"
    assert run.plan is not None
    assert run.plan.approval == Decision.PENDING


@pytest.mark.asyncio
async def test_rejected_plan_fails_run(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def failure(): pass\n", encoding="utf-8")
    orchestrator = Orchestrator()
    run = orchestrator.create(Issue(repository="demo", number=2, title="Failure"))
    await orchestrator.analyze(run.id, tmp_path)
    run = orchestrator.approve_plan(run.id, False, "scope is too broad")
    assert run.state == RunState.FAILED
    assert run.plan and run.plan.approval == Decision.REJECTED


def test_invalid_transition_is_rejected() -> None:
    orchestrator = Orchestrator()
    run = orchestrator.create(Issue(repository="demo", number=1, title="Bug"))
    with pytest.raises(InvalidTransition):
        orchestrator.approve_plan(run.id, True)
