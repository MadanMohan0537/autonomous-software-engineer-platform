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

The runtime in `src/ase` is a single Python process (API, CLI or worker) over one SQLite
database. Every interface below is a Protocol, and each has the implementation that ships
today plus the replacement it was designed for:

| Interface | Ships today | Designed replacement |
|---|---|---|
| `PlatformStore` (runs + traces) | `SQLiteRunStore` | PostgreSQL event store |
| `Sandbox` | `LocalSandbox` (development), `DockerSandbox` | microVM runner |
| `ModelClient` | `AnthropicClient` (Messages API), `ScriptedClient` (tests) | any provider |
| `Embedder` | `HashingEmbedder`, `VoyageEmbedder` | pgvector-backed index |
| `SourceParser` | tree-sitter (with the `index` extra), stdlib `ast` | more grammars |
| workflow executor | `SequentialRunner`, LangGraph `StateGraph` (with the `graph` extra) | – |

The local sandbox is not a production security boundary; `Settings.validate_production()`
refuses it. The module-by-module design, and the decisions behind it, are in
[DESIGN.md](DESIGN.md) and [adr/](adr/README.md).

### Package layout

```text
src/ase/
├── contracts.py        typed records: Issue, Task, AgentRun, Step, Patch, TestReport, Review, EvalResult
├── store.py            PlatformStore protocol, in-memory store, SQLite wrapper
├── persistence.py      SQLite schema: runs, traces, task queue, webhook deliveries
├── policy.py           .ase/policy.yaml: commands, paths, scope, authority (fail closed)
├── sandbox.py          argv-only LocalSandbox and DockerSandbox
├── workspace.py        per-run git worktrees
├── testrun.py          pytest execution and JUnit parsing
├── config.py           Settings.from_env, production validation
├── repo_intelligence/  module 1: parsers, chunks, BM25, embeddings, vectors, graph, index, retrievers
├── llm/                ModelClient protocol, Anthropic wire client, scripted client, pricing
├── agent/              module 2: state, prompts, tools, nodes, runner, LangGraph graph, delivery, service
├── testing/            module 3: coverage, AST mutation testing, validated test generation
├── ci/                 module 4: Actions API, triage, watcher, canary decision engine
├── evals/              module 5: suites, harvesting, clean-checkout grading, runner, reports, SWE-bench
├── feedback/           module 6: review sync, features, scorers, dataset, LoRA gate, reviewer ledger
├── github.py           async client for issues; sync Git Data / PR / Actions API client
├── api.py              FastAPI control plane: runs, gates, trace, IDE, feedback, webhooks
├── cli.py              `ase` command groups for every module
├── ide.py, webhooks.py, worker.py, observability.py   console, GitHub events, queue worker, telemetry
└── model.py, model_planner.py, patching.py, mutation.py, test_generation.py
                        OpenAI-compatible planner/test-proposal adapters, diff application, mutmut wrapper
```

## Event model

Every run records ordered events with a sequence number, workflow state, kind,
payload, and UTC timestamp. State mutations are persisted after each externally
meaningful transition. Production storage should append events transactionally and
derive read models from the event stream.

