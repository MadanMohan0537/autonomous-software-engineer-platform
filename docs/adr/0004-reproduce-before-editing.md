# ADR 0004: a run installs a failing reproduction test before it edits code

**Status:** accepted

## Context

An agent that edits first and tests later can "fix" an issue it never reproduced, and a
green suite proves nothing about the reported bug. Evaluation tasks with hidden tests
make this worse: the agent has no acceptance test at all unless it writes one.

## Decision

The planner proposes a reproduction test alongside the plan. `create_workspace` writes
it into the fresh worktree and keeps it only if it *fails on the base commit*; a test that
passes is dropped and the drop is recorded. The file's digest is tracked in the run
state, and `verify` fails the run if the agent modifies or removes it. The reproduction
test joins the fail-to-pass set the agent is judged against.

## Consequences

- A submitted patch is accompanied by a test that was red before and is green after.
- Some issues cannot be reproduced by a unit test; the planner may return none, and
  the run proceeds with only the task's own tests.
- The agent's `edit_file` tool refuses test files (unless the task is about tests), so it
  cannot weaken the reproduction to make it pass.
