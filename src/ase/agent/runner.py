"""Sequential executor for the workflow nodes, with pluggable approval gates.

This runs the exact node functions and routing that the LangGraph graph runs, without
durability or interrupts. It is what the tests use, what a zero-dependency install uses,
and a readable spec of the workflow: read `run()` top to bottom.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from ase.agent.nodes import (
    AgentNodes,
    route_after_plan_decision,
    route_after_pr_decision,
    route_after_reflect,
)
from ase.agent.state import AgentState

MAX_LOOPS = 50


class ApprovalGate(Protocol):
    def decide(self, kind: str, state: AgentState) -> dict[str, Any]: ...


class AutoApprove:
    """Approves every gate. For evaluation runs and tests, never for real repositories."""

    def decide(self, kind: str, state: AgentState) -> dict[str, Any]:
        return {"approved": True, "reason": f"auto-approved {kind}"}


class RejectAll:
    def decide(self, kind: str, state: AgentState) -> dict[str, Any]:
        return {"approved": False, "reason": f"rejected {kind}"}


class CallbackGate:
    def __init__(self, callback: Callable[[str, AgentState], dict[str, Any]]) -> None:
        self._callback = callback

    def decide(self, kind: str, state: AgentState) -> dict[str, Any]:
        return self._callback(kind, state)


def merge(state: AgentState, update: dict[str, Any]) -> AgentState:
    merged: dict[str, Any] = dict(state)
    merged.update(update)
    return cast(AgentState, merged)


class SequentialRunner:
    def __init__(self, nodes: AgentNodes, gate: ApprovalGate | None = None) -> None:
        self.nodes = nodes
        self.gate = gate or AutoApprove()

    def run(self, state: AgentState) -> AgentState:
        nodes = self.nodes
        state = merge(state, nodes.intake(state))
        state = merge(state, nodes.retrieve(state))
        state = merge(state, nodes.propose_plan(state))
        state = merge(state, {"plan_decision": self.gate.decide("plan", state)})
        state = merge(state, nodes.apply_plan_decision(state))
        if route_after_plan_decision(state) == "end":
            return state
        state = merge(state, nodes.create_workspace(state))
        for _ in range(MAX_LOOPS):
            state = merge(state, nodes.implement(state))
            state = merge(state, nodes.verify(state))
            state = merge(state, nodes.reflect(state))
            route = route_after_reflect(state)
            if route == "implement":
                continue
            if route == "end":
                return state
            break
        state = merge(state, {"pr_decision": self.gate.decide("pr", state)})
        state = merge(state, nodes.apply_pr_decision(state))
        if route_after_pr_decision(state) == "end":
            return state
        return merge(state, nodes.open_draft_pr(state))
