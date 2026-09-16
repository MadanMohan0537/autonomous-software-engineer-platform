# Architecture decision records

One file per decision that shaped `src/ase` and would be expensive to reverse. Each
record states the context, the decision, and the consequences we accept. A superseded
record stays in place with a pointer to its successor.

| ADR | Decision |
|---|---|
| [0001](0001-trace-store-is-the-spine.md) | The trace store is the integration point between modules |
| [0002](0002-nodes-independent-of-langgraph.md) | Workflow nodes are plain functions; LangGraph is an optional executor |
| [0003](0003-argv-only-sandbox.md) | Sandboxes accept argv arrays only, never a shell string |
| [0004](0004-reproduce-before-editing.md) | A run installs a failing reproduction test before it edits code |
| [0005](0005-generated-tests-must-kill-mutants.md) | A generated test is kept only if it kills a mutant |
| [0006](0006-grade-on-clean-checkout.md) | Evaluation grades on a clean checkout, never on the agent's run |
| [0007](0007-reward-model-rungs.md) | Reward modelling is a ladder of rungs gated on labels |
| [0008](0008-messages-api-over-httpx.md) | The model client speaks the Messages API over httpx, not an SDK |
| [0009](0009-no-merge-no-deploy.md) | The platform has no merge or deploy authority |
