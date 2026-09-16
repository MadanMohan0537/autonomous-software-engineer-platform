# ADR 0006: evaluation grades on a clean checkout, never on the agent's run

**Status:** accepted

## Context

The agent runs tests while it works, and its last test report is usually green when it
submits. Trusting that report for the evaluation score would let any bug in the agent's
own test invocation (wrong working directory, a cached `.pyc`, an edited test) inflate the
number.

## Decision

`ase.evals.grade.Grader` checks out the task's base commit in a fresh worktree, applies
the submitted patch, installs the task's hidden test files, and runs the fail-to-pass and
pass-to-pass tests in a sandbox. Resolution is the SWE-bench definition: every
fail-to-pass test passes and every pass-to-pass test still passes. For SWE-bench proper,
the platform only writes the predictions file; the official harness grades.

## Consequences

- Every eval run costs one extra checkout and test run per task.
- Suites are committed JSON, harvested from git history with fail-to-pass status
  measured rather than assumed, so a number can always be traced to its tasks.
- No SWE-bench number is reported from this repository's own grader.
