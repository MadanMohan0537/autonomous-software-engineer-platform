"""LangGraph wiring: checkpoints and human interrupts. Skipped when the extra is absent."""

from __future__ import annotations

from pathlib import Path

import pytest

from ase.agent.graph import GraphRunner, langgraph_available
from ase.agent.nodes import AgentNodes
from ase.contracts import RunState
from ase.llm import text_completion, tool_completion
from tests.test_agent_flow import EDIT, build, plan_completion, start

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


def test_graph_pauses_at_both_gates_and_resumes(fixture_repo: Path, tmp_path: Path) -> None:
    script = [plan_completion(), tool_completion("edit_file", EDIT), text_completion("done")]
    services, store, _ = build(fixture_repo, tmp_path, script)
    state = start(services, fixture_repo)
    runner = GraphRunner(AgentNodes(services))

    runner.start(state, state["run_id"])
    pending = runner.pending(state["run_id"])
    assert pending is not None and pending["kind"] == "plan"
    assert store.get(state["run_id"]).state == RunState.AWAIT_PLAN_APPROVAL  # type: ignore[union-attr]

    runner.resume(state["run_id"], {"approved": True, "reason": "plan is bounded"})
    pending = runner.pending(state["run_id"])
    assert pending is not None and pending["kind"] == "pr"
    assert pending["report"]["passed"] >= 1
    assert store.get(state["run_id"]).state == RunState.AWAIT_PR_APPROVAL  # type: ignore[union-attr]

    final = runner.resume(state["run_id"], {"approved": True, "reason": "ship it"})
    assert final["outcome"] == "submitted" and runner.pending(state["run_id"]) is None
    assert runner.state(state["run_id"])["outcome"] == "submitted"
    assert store.get(state["run_id"]).state == RunState.COMPLETED  # type: ignore[union-attr]


def test_graph_rejection_ends_the_run(fixture_repo: Path, tmp_path: Path) -> None:
    services, store, _ = build(fixture_repo, tmp_path, [plan_completion()])
    state = start(services, fixture_repo)
    runner = GraphRunner(AgentNodes(services))
    runner.start(state, state["run_id"])
    final = runner.resume(state["run_id"], {"approved": False, "reason": "too broad"})
    assert final["outcome"] == "rejected"
    assert store.get(state["run_id"]).state == RunState.FAILED  # type: ignore[union-attr]
