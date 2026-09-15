# Architecture

## System boundary

The platform is a control plane. It coordinates repository analysis, planning,
isolated execution, verification, approval, and GitHub delivery. Model output is
treated as an untrusted proposal. Deterministic policy code controls authority.

## Components

| Component | Owns | Does not own |
|---|---|---|
| API | Run creation, read models, approval requests | Patch generation |
| Orchestrator | State transitions and checkpoints | Command authorization |
| Repository intelligence | Files, symbols, relations, context ranking | Code edits |
| Planner | Proposed steps and assumptions | Execution permission |
| Policy engine | Commands, paths, scope, authority | Model reasoning |
| Sandbox | Process isolation and evidence capture | Merge decisions |
| Verifier | Checks and evaluation reports | Human approval |
| GitHub adapter | Draft-PR API boundary | Autonomous merge |

## Trust boundaries

1. Issue text and repository contents are untrusted inputs.
2. Model-generated plans, patches, commands, and tests are untrusted proposals.
3. The policy engine runs outside the model boundary.
4. Execution occurs in an ephemeral workspace with minimal credentials.
5. GitHub credentials are available only to the delivery adapter.
6. A verified patch still requires a named human approval.

## Current runtime

The first release uses an in-process store and a constrained local runner to keep
development reproducible. These interfaces are intentionally replaceable:

- `RunStore` → PostgreSQL event store
- `LocalSandbox` → container or microVM runner
- `DeterministicPlanner` → model-backed planner
- lexical index → Tree-sitter plus pgvector index

The local runner is not described as a production security boundary.

## Event model

Every run records ordered events with a sequence number, workflow state, kind,
payload, and UTC timestamp. State mutations are persisted after each externally
meaningful transition. Production storage should append events transactionally and
derive read models from the event stream.

