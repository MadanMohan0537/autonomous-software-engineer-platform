# ADR 0002: workflow nodes are plain functions; LangGraph is an optional executor

**Status:** accepted

## Context

LangGraph gives the workflow durability (a checkpoint after every node) and human gates
(`interrupt()`), which is exactly what a run that pauses for approval needs. It also
pulls in a dependency tree that cannot be installed everywhere, and a framework-shaped
node is hard to unit test.

## Decision

`ase.agent.nodes.AgentNodes` are plain methods: `AgentState` in, partial state out,
services injected. Routing functions are plain functions. `ase.agent.runner.
SequentialRunner` executes them in-process with approval callbacks; `ase.agent.graph.
build_graph` wires the same nodes and routers into a `StateGraph` with a SQLite
checkpointer and interrupts at the two gates. The LangGraph import is guarded, and the
CLI selects the graph with `--graph`.

## Consequences

- The workflow is tested once, offline, with a scripted model client; the graph tests
  only check wiring and resume semantics.
- Reading `SequentialRunner.run()` top to bottom is the specification of the workflow.
- Two executors must be kept in step; the routing functions are shared so the branch
  logic cannot drift, but the node order in `run()` is duplicated on purpose.
