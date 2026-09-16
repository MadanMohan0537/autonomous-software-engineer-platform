# Design: the six modules and the spine that connects them

This document is the working design for the platform as it exists in `src/ase`. It
describes what each module does, where the boundaries are, and which decisions were made
deliberately (each of those has an ADR under `docs/adr/`). Where a capability is a plan
rather than code, it says so; `README.md` keeps the authoritative implementation status.

## The one-sentence design

A GitHub issue becomes a governed run; the run leaves a complete trace (steps, patches,
test reports, reviews, evaluation results); every other module either feeds that trace
or reads from it.

```text
                 ┌────────────────────────────────────────────────────┐
                 │  Trace store (SQLite, ase.persistence)             │
                 │  agent_runs · steps · patches · test_reports ·     │
                 │  reviews · eval_results                            │
                 └───▲──────────▲──────────▲──────────▲──────────▲────┘
                     │          │          │          │          │
   1 repo            2 issue →  3 tests &  4 CI/CD    5 eval     6 human
   intelligence      PR agent   mutation   agent      harness    feedback
   (reads code)      (writes)   (writes)   (writes)   (reads+    (reads+
                                                       writes)    writes)
```

The trace store is the spine. It is why the evaluation harness can compare two
configurations of the agent, why the feedback module can build a labelled dataset without
a separate logging pipeline, and why a reviewer can reconstruct any run.

## Module 1: repository intelligence (`ase.repo_intelligence`)

**Goal.** Give the agent the right few hundred lines of a repository it has never seen.

**Pipeline.** `RepositoryIndexer` walks the repository (skipping build and cache
directories), records every supported file with its digest and lexical tokens, and parses
it. Python goes through `parsers.select_parser()`: tree-sitter when the `index` extra is
installed, the standard-library `ast` otherwise; both produce the same `Definition`
records (exact line spans, docstrings, parent) and the same `Symbol` and `Relationship`
records (imports and calls). Other languages get regex-based, line-level symbols from
`language_parser.py`; they are retrievable but have no call graph.

`KnowledgeIndex.build()` turns the file records into:

- **Chunks** (`chunks.py`): one chunk per top-level definition when it fits the token
  budget, otherwise split at child definitions or into fixed windows; file headers and
  trailers are their own chunks. Non-Python files are chunked by window.
- **A lexical index** (`lexical.py`): a hand-written BM25 over identifier-aware tokens.
  Writing it rather than importing it keeps the ranking function readable and testable.
- **A vector index** (`vectors.py`, `embeddings.py`): numpy matrix of unit vectors.
  `HashingEmbedder` is the zero-dependency default (feature hashing of unigrams and
  bigrams); `VoyageEmbedder` (voyage-code-3) is used when `VOYAGE_API_KEY` is set.
- **A code graph** (`graph.py`): networkx digraph of files, symbols, calls and imports.
  Personalised PageRank from the issue's terms ranks symbols, and the top slice is
  rendered as the *repo map*: an outline, not code, so it costs a few hundred tokens.

`KnowledgeIndex.search` fuses the lexical and vector rankings with reciprocal rank
fusion, adds a bonus when a chunk's symbol name matches the issue's terms, and
down-weights test files (`TEST_FILE_WEIGHT`). `KnowledgeRetriever` aggregates chunk hits
into file-level context (best chunk plus a small bonus for the rest, so a file with many
tiny chunks cannot win by volume) and attaches a human-readable reason to every item;
the code graph contributes separately, as the repo map the agent sees. Everything is
keyed by the commit SHA it was built from: an index for a different SHA is a different
index, and the agent never retrieves stale code.

**Evaluation.** `retrieval_eval.py` plus `ase knowledge eval` measure recall@k against
the repository's own history: for each recent commit, do the changed files appear in the
top-k results for the commit message? This is the same signal the eval harvester uses.

## Module 2: issue-to-PR agent (`ase.agent`)

**Goal.** Take a task, produce a patch with evidence, stop at two human gates, and open a
draft pull request.

**Nodes** (`nodes.py`) are pure functions of `AgentState` plus injected services. They
know nothing about LangGraph, and the same functions run in two executors:

- `SequentialRunner` (`runner.py`): in-process, with pluggable approval callbacks. Used by
  tests, the evaluation harness, and zero-dependency installs. Reading `run()` top to
  bottom is the spec of the workflow.
- `build_graph` (`graph.py`): a LangGraph `StateGraph` with a SQLite checkpoint after
  every node and `interrupt()` at the two gates, so a run survives a crash and can be
  resumed by a different process (`ase resume <run_id> --approve`).

```text
intake → retrieve → propose_plan → [await_plan_approval] → create_workspace
      → implement → verify → reflect ─┬─ submit → [await_pr_approval] → open_draft_pr
                                      ├─ retry  → implement
                                      └─ give_up → end
```

**Reproduce before editing.** The planner (`prompts.PLAN_SYSTEM`) proposes both a change
plan and a *reproduction test*. `create_workspace` writes that test into the fresh
worktree and keeps it only if it fails on the base commit; a reproduction that passes is
dropped and recorded as such. The test's digest is tracked so that `verify` fails the run
if the agent later edits or deletes it.

**Constrained tools** (`tools.py`). `read_file`, `list_files`, `search_code` (backed by
the knowledge index when available), `edit_file` (search/replace blocks, re-parsed before
writing so a syntax error never reaches the test step) and `run_command` (an argv array
through the policy engine and the sandbox; no shell, no network, no credentials).
`edit_file` refuses test files unless the task is about tests. A `command_only` tool mode
exists so the evaluation harness can measure what the structured tools are worth.

**Verify** runs the fail-to-pass and pass-to-pass tests through `PytestRunner` (JUnit XML
parsed into a `TestReport`), runs the full suite when no pass-to-pass set is known, then
applies the deterministic checks: patch-scope policy and test integrity. **Reflect**
decides `submit`, `retry` or `give_up`: green plus policy-clean submits; an exhausted
budget, two identical patches in a row, or a missing report gives up; otherwise the small
classifier model reads the failure and picks a focus for the next iteration.

**Delivery** (`delivery.py`, `pr.py`). The agent never holds the GitHub token and never
runs `git push`. After the PR gate, the changed files are written through the Git Data API
(blobs, tree on the base commit, commit, ref) and a *draft* pull request is opened with the
plan, the test report and the trace summary in its body.

**Budgets.** `RunConfig.budget` caps iterations, tokens, dollars and wall-clock time;
`AgentRun.budget_exceeded()` is checked in `reflect` and turns an over-budget run into a
`FAILED` run with a recorded reason, not a silent stop.

## Module 3: test generation and mutation testing (`ase.testing`)

**Goal.** Find where the test suite is weak, and add tests that provably strengthen it.

**Mutation testing** (`mutation.py`) has a built-in AST mutator (comparison, arithmetic,
boolean, constant and negation operators) rather than a dependency; `MutationRunner` runs
the chosen tests against each mutant in the sandbox with a per-file cap and a wall-clock
budget, and reports the score and the surviving mutants with their locations.
`survivors_to_tasks` turns survivors into `Task`s so the agent can be pointed at them.
(`ase.mutation` is a thin wrapper for an external `mutmut` run, kept from the earlier
track for people who already use that tool.)

**Coverage-guided generation** (`generate.py`, `coverage_report.py`). `CoverageRunner`
collects line coverage in the sandbox; `TestGenerator` asks the coder model for tests
targeting the uncovered regions of the chosen files, installs each candidate, and keeps it
only if it passes on the current code **and kills at least one mutant** of the file it
targets. A test that passes but kills nothing is a tautology and is discarded with its
reason recorded. This is the rule that keeps generated tests from inflating coverage
without checking anything.

## Module 4: CI/CD agent (`ase.ci`)

**Goal.** Turn a red build on one of the agent's pull requests into the right next
action, and teach progressive delivery without ever deploying.

**Triage** (`triage.py`) is rules first, model second: deterministic patterns recognise
lint failures, failing tests, missing modules and network blips; only an unrecognised log
excerpt goes to the classifier model. Routing fails closed: anything unknown is escalated
to a human, never retried blindly and never turned into an agent task.

**Watcher** (`watcher.py`) polls workflow runs for the agent's draft PRs through the
GitHub Actions API, applies triage, and either labels the PR, re-runs a flaky job once,
re-enters a task through the full governed workflow (gates included), or escalates.

**Canary** (`canary.py`) is a decision engine and a simulator: SLO windows, weight
step-ups, automatic rollback decisions. `CanaryController.deploy` fails closed against
the repository policy, which denies `deploy_production`. `ase canary simulate` replays a
JSON list of windows and prints the decisions.

## Module 5: evaluation harness (`ase.evals`)

**Goal.** Numbers that can be traced to tasks, graded the way SWE-bench grades.

**Suites** (`suites.py`) are committed JSON. **Harvesting** (`harvest.py`) builds suites
from the repository's own history: any commit that changed both production code and tests
becomes a task whose base is the parent commit and whose acceptance tests are the ones the
commit touched; fail-to-pass status is *measured* by running the tests on both sides, not
assumed. Merged pull requests can be harvested through the GitHub API the same way.

**Grading** (`grade.py`) never trusts the agent's own test run. It checks out the base
commit in a fresh worktree, applies the patch, installs the hidden test patch, and runs
fail-to-pass and pass-to-pass in a sandbox. `EvalRunner` (`runner.py`) drives the
sequential runner over a suite under a named `RunConfig` with both gates auto-approved,
records an `EvalResult` per task, and `report.py` renders resolve rate, cost, tokens and
iterations per configuration, with per-case tables on request.

**SWE-bench** (`swebench.py`) loads instances, writes the predictions file, and returns
the official harness command. The platform does not grade SWE-bench itself.

## Module 6: human feedback loop (`ase.feedback`)

**Goal.** Every review is a label; scorers estimate the probability a human approves a
trajectory; the learned rungs are gated on data that actually exists.

`ReviewSync` (`reviews.py`) mirrors PR reviews, review comments and merge state into the
`reviews` table; `human_label` (`dataset.py`) collapses them into approve / reject / mixed
labels and `build_dataset` joins labels with trajectory features (`features.py`): test
delta, patch size and spread, iterations, cost, whether tests were touched, whether the
reproduction test survived. `FeedbackStore` (`ledger.py`) keeps the console's structured
reviewer feedback (reasons like "unnecessary refactor" or "missed edge case").

Three scorer rungs read the same features: `HeuristicScorer` (fixed weights, zero labels),
`LogisticScorer` (weights fitted by gradient descent; `train_logistic` refuses fewer than
`MIN_TRAINING_EXAMPLES` or one-sided datasets), and the LoRA trajectory classifier in
`lora.py`, which validates the dataset, writes a training manifest, and stops. It is
gated on `MIN_HUMAN_LABELS` (200) reviewed labels and its training loop is deliberately
outside this package. `agreement` measures
each scorer against held-out human labels; `rank_candidates` is how a scorer would be used
at run time. The diff-scan features stay in the feature set even once a learned model
exists, so a reward model can never learn to like patches that skip tests.

## Cross-cutting

**Policy** (`ase.policy`). `.ase/policy.yaml` per repository, fail-closed defaults:
an executable allowlist plus denied `git` subcommands, denied path segments (`.git`,
`.env`, `.ssh`, ...), a patch-scope check on changed files, test edits only for test
tasks, `allow_merge: false`, `allow_deploy: false`, network denied.
`PolicyEngine.authority(action)` is the single place merge and deploy are refused.

**Sandbox** (`ase.sandbox`). Both backends take argv arrays only. `LocalSandbox` is the
development runner and is not a security boundary. `DockerSandbox` runs
`--network none --cpus --memory --pids-limit --security-opt no-new-privileges --cap-drop
ALL --user 10001:10001` with the worktree bind-mounted at `/workspace`
(`docker/sandbox.Dockerfile`). Credentials never enter either.

**Model access** (`ase.llm`). One `ModelClient` protocol; `AnthropicClient` speaks the
Messages API over httpx with tool use, prompt-cache markers and usage accounting;
`ScriptedClient` replays canned completions so every workflow test runs offline.
`pricing.py` turns usage into dollars for the budget.

**Configuration** (`ase.config`). Everything from the environment, with
`validate_production()` refusing the local sandbox, sandbox networking, or a missing
webhook secret.

## What is deliberately not here

- No autonomous merge, no push to protected branches, no deployment: the policy engine
  denies the actions and the code paths that would need them do not exist.
- No trained reward model: the rungs that need labels are gated on the labels.
- No SWE-bench score from this repository's grader: the official harness grades.
- No PostgreSQL, Redis or Kubernetes-specific code paths: the interfaces (`PlatformStore`,
  `Sandbox`, `ModelClient`, `Embedder`) are where those would plug in.
