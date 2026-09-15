"""The durable workflow: LangGraph `StateGraph` over the same nodes the runner uses.

What LangGraph adds on top of `SequentialRunner`:

* a checkpoint after every node (SQLite), so a crashed run resumes from its last node;
* `interrupt()` at the two human gates, so a run pauses until someone records a decision
  and the process that resumes it can be a different one from the process that started it.

The import is guarded: the rest of the platform works without LangGraph installed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

from ase.agent.nodes import (
    AgentNodes,
    route_after_plan_decision,
    route_after_pr_decision,
    route_after_reflect,
)
from ase.agent.state import AgentState

LANGGRAPH_MISSING = (
    "LangGraph is not installed. Install the 'graph' extra (pip install -e '.[graph]') or use "
    "ase.agent.runner.SequentialRunner, which executes the same nodes without checkpoints."
)


def langgraph_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
        import langgraph.types  # noqa: F401
    except ImportError:
        return False
    return True


def build_graph(nodes: AgentNodes) -> Any:
    """Return an uncompiled `StateGraph` wired exactly like `SequentialRunner.run`."""
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(LANGGRAPH_MISSING) from exc

    policy = nodes.services.policy.policy

    def await_plan_approval(state: AgentState) -> dict[str, Any]:
        if not policy.require_plan_approval:
            return {"plan_decision": {"approved": True, "reason": "policy: automatic"}}
        decision = interrupt({"kind": "plan", "run_id": state["run_id"], "plan": state.get("plan")})
        return {"plan_decision": dict(decision)}

    def await_pr_approval(state: AgentState) -> dict[str, Any]:
        if not policy.require_pr_approval:
            return {"pr_decision": {"approved": True, "reason": "policy: automatic"}}
        decision = interrupt(
            {
                "kind": "pr",
                "run_id": state["run_id"],
                "patch": state.get("patch"),
                "report": state.get("report"),
            }
        )
        return {"pr_decision": dict(decision)}

    graph = StateGraph(AgentState)
    graph.add_node("intake", nodes.intake)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("propose_plan", nodes.propose_plan)
    graph.add_node("await_plan_approval", await_plan_approval)
    graph.add_node("apply_plan_decision", nodes.apply_plan_decision)
    graph.add_node("create_workspace", nodes.create_workspace)
    graph.add_node("implement", nodes.implement)
    graph.add_node("verify", nodes.verify)
    graph.add_node("reflect", nodes.reflect)
    graph.add_node("await_pr_approval", await_pr_approval)
    graph.add_node("apply_pr_decision", nodes.apply_pr_decision)
    graph.add_node("open_draft_pr", nodes.open_draft_pr)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "retrieve")
    graph.add_edge("retrieve", "propose_plan")
    graph.add_edge("propose_plan", "await_plan_approval")
    graph.add_edge("await_plan_approval", "apply_plan_decision")
    graph.add_conditional_edges(
        "apply_plan_decision",
        route_after_plan_decision,
        {"create_workspace": "create_workspace", "end": END},
    )
    graph.add_edge("create_workspace", "implement")
    graph.add_edge("implement", "verify")
    graph.add_edge("verify", "reflect")
    graph.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"implement": "implement", "await_pr_approval": "await_pr_approval", "end": END},
    )
    graph.add_edge("await_pr_approval", "apply_pr_decision")
    graph.add_conditional_edges(
        "apply_pr_decision",
        route_after_pr_decision,
        {"open_draft_pr": "open_draft_pr", "end": END},
    )
    graph.add_edge("open_draft_pr", END)
    return graph


def sqlite_checkpointer(path: Path) -> Any:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(LANGGRAPH_MISSING) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


def _as_state(values: dict[str, Any]) -> AgentState:
    return cast(AgentState, {k: v for k, v in values.items() if not k.startswith("__")})


class GraphRunner:
    """Start, inspect and resume checkpointed runs. One thread id per agent run."""

    def __init__(self, nodes: AgentNodes, checkpointer: Any | None = None) -> None:
        if checkpointer is None:
            try:
                from langgraph.checkpoint.memory import InMemorySaver
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(LANGGRAPH_MISSING) from exc
            checkpointer = InMemorySaver()
        self.app = build_graph(nodes).compile(checkpointer=checkpointer)

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, state: AgentState, thread_id: str) -> AgentState:
        result = self.app.invoke(dict(state), config=self._config(thread_id))
        return _as_state(result)

    def pending(self, thread_id: str) -> dict[str, Any] | None:
        """The interrupt payload a paused run is waiting on, or None."""
        snapshot = self.app.get_state(self._config(thread_id))
        for task in getattr(snapshot, "tasks", ()):
            interrupts = getattr(task, "interrupts", ())
            if interrupts:
                value = interrupts[0].value
                return dict(value) if isinstance(value, dict) else {"value": value}
        return None

    def resume(self, thread_id: str, decision: dict[str, Any]) -> AgentState:
        from langgraph.types import Command

        result = self.app.invoke(Command(resume=decision), config=self._config(thread_id))
        return _as_state(result)

    def state(self, thread_id: str) -> AgentState:
        snapshot = self.app.get_state(self._config(thread_id))
        return _as_state(dict(getattr(snapshot, "values", {}) or {}))
