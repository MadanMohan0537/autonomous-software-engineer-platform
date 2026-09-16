# ADR 0001: the trace store is the integration point between modules

**Status:** accepted

## Context

Six modules need to exchange data: the agent produces patches and test reports, the
evaluation harness needs to compare configurations, the CI agent needs to find the runs
behind a pull request, and the feedback loop needs trajectories joined with review
outcomes. Point-to-point interfaces between six modules would be fifteen edges.

## Decision

Every module reads from and writes to one store (`ase.store.PlatformStore`, implemented
by `ase.persistence.SQLiteRunStore`) with a fixed set of trace records defined in
`ase.contracts`: `AgentRun`, `Step`, `Patch`, `TestReport`, `Review`, `EvalResult`. A
module never calls another module to get data another module produced; it reads the
trace. Records are append-only except `AgentRun`, which is the mutable read model.

## Consequences

- Adding a module means adding a record type, not an interface between modules.
- Every number in an evaluation report or a feedback dataset can be traced to rows.
- `export_jsonl` makes the whole store portable for offline analysis and for training.
- SQLite is enough for a single-host deployment; a PostgreSQL implementation of the same
  protocol is the upgrade path, and nothing outside `persistence.py` would change.
