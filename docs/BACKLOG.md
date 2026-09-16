# Backlog: next weekend

Ten pieces of work that each fit in a few hours, ordered by how much they teach. Each has
an acceptance criterion that a test or a command can check, so "done" is not a matter of
opinion. They are meant to be opened as GitHub issues; the text here is the issue body.

## 1. Mutation score in the PR body

The `verify` node runs the tests; it does not yet run mutation testing on the files the
patch touched, so a reviewer cannot see how strong the tests behind a patch are.

- `verify` runs `MutationRunner` on the patched source files with a 120 s budget and
  records the score on the `TestReport`.
- The draft PR body shows `mutation score: N/M (survivors: path:line, ...)`.
- A patch whose only test is a tautology (score 0 with mutants generated) fails the
  `test-strength` check; the threshold is a policy value with a documented default.

## 2. Incremental knowledge index

`ase knowledge build` re-parses and re-embeds every file on every commit. On a real
repository that is minutes per run.

- The index stores the file digest per chunk; a rebuild at a new SHA re-processes only
  files whose digest changed, and drops chunks of deleted files.
- `ase knowledge build .` on this repository after a one-file change takes under two
  seconds on the hashing embedder (measured in a test with a fake clock or a timer
  assertion with a generous bound).
- The recall@k number on `ase knowledge eval` is unchanged.

## 3. Call graphs for TypeScript and Go

Non-Python files have line-level symbols only. The retrieval and the repo map both lose
the edges that matter most in polyglot repositories.

- `SourceParser` implementations for TypeScript and Go using their tree-sitter grammars
  (new optional extra), producing `Definition`, `Symbol` and `Relationship` records with
  the same semantics as the Python parser.
- `ase index` on a fixture repository with a TS file that imports and calls a function
  yields `imports` and `calls` relationships; the AST fallback for those languages stays
  the regex parser.
- The design doc's "no call graph for other languages" line is removed.

## 4. Record/replay cassette for a live-model integration test

Every workflow test uses the scripted client, so a prompt regression that only a real
model would expose goes unnoticed until someone runs `ase run` by hand.

- A `RecordingClient` wraps `AnthropicClient`, writes request/response pairs to a
  cassette file, and a `ReplayClient` serves them back; cassettes are committed for one
  end-to-end run on a fixture issue.
- CI runs the replay test offline; a manual workflow re-records with `ANTHROPIC_API_KEY`
  from repository secrets and opens a PR with the new cassette.
- The recorded run resolves the fixture issue and the trace has ≥ 1 tool call per node.

## 5. Console: PR gate and trace timeline

The API has `POST /api/runs/{id}/pr-review` and `GET /api/runs/{id}/trace`; the IDE
console only knows the plan gate.

- The run inspector shows steps (node, model, tokens, cost, tool calls) and test reports
  from the trace endpoint, and the patch diff.
- An "approve PR / request changes" control posts to `pr-review` with a reason, and the
  timeline shows the decision with its reviewer.
- `tests/test_ide.py` covers the new read model; a browser smoke test is optional.

## 6. CI watcher as a worker task

`ase ci check <run_id>` is manual. The worker has a task queue; red builds should find
their own way to triage.

- The GitHub `workflow_run` webhook enqueues a `ci_check` task for runs whose PR matches
  the completed workflow; the worker executes `CiWatcher.check`.
- A re-entered task (a failing test the agent can fix) is created through the same
  `create_run` path as an issue, with the gates intact.
- `tests/test_worker.py` covers the new task kind with a fake API.

## 7. Console feedback joins the dataset

`FeedbackStore` (reviewer reasons from the console) and `build_dataset` (labels from
GitHub reviews) do not meet. Structured reasons are the most valuable labels we have.

- `build_dataset` joins `FeedbackStore` decisions to trajectories by run id; a console
  rejection with a reason is a `reject` label with the reason kept in `metadata`.
- `ase feedback dataset` reports how many labels came from GitHub vs the console.
- `agreement` can be filtered by label source.

## 8. First SWE-bench Lite run, documented

`ase.evals.swebench` writes a predictions file and prints the harness command, and
nobody has run it.

- A `docs/EVALUATION.md` section walks through 10 SWE-bench Lite instances: the
  `ase eval run` invocation, wall-clock, cost from the trace store, and the official
  harness's resolved count, with the predictions file committed under `evals/results/`.
- The README's "no SWE-bench score" line is replaced by the measured number with a link
  to the run, and the caveat that it is 10 instances.

## 9. Docker sandbox integration test

`DockerSandbox` builds the right argv; nothing verifies the image and the flags actually
work together.

- A pytest marker `docker` (skipped when the daemon is absent) builds
  `docker/sandbox.Dockerfile` and runs the fixture repository's tests inside it through
  `DockerSandbox`, asserting the JUnit report is parsed and that `curl` to any host fails
  (network is `none`).
- CI runs the marked test in a separate job on `ubuntu-latest` where Docker is available.

## 10. PostgreSQL implementation of `PlatformStore`

SQLite is right for one host. The Protocol was designed for the swap; make it real.

- `ase.persistence_postgres.PostgresRunStore` implements `PlatformStore` with the same
  tables (JSONB bodies) and passes the same test module as the SQLite store,
  parameterised over both backends; the Postgres tests skip without `ASE_TEST_DATABASE_URL`.
- The k8s manifests gain a Postgres option; `Settings` reads `ASE_DATABASE_URL`.
- `export_jsonl` produces identical output from either backend for the same data.
