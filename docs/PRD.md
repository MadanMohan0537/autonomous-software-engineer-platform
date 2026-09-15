# Product Requirements

## Objective

Enable an engineer to submit a bounded GitHub issue and receive an evidence-backed
draft pull request without giving a model merge or deployment authority.

## Primary user

An engineer or technical lead responsible for reviewing repository changes.

## Initial job to be done

When a well-scoped defect is reported, help me identify the affected code, reproduce
the behavior, propose a minimal plan, implement an isolated patch, and verify it so I
can review a draft pull request with less investigative work.

## Functional requirements

1. Index supported source files and extract Python symbols and relationships.
2. Rank relevant files with human-readable reasons.
3. Preserve a typed issue-to-run lineage.
4. Pause after planning until a human records a decision.
5. Reject commands and paths outside repository policy.
6. Capture command inputs, outputs, duration, and exit status.
7. Run patch-scope, test-integrity, lint, type, and test checks.
8. Produce a draft-PR body containing verification evidence and run identity.
9. Expose run state and evidence through an API and review console.

## Non-goals for version 0.1

- Autonomous merging or production deployment
- A claim of secure multi-tenant isolation
- Training a reward model from insufficient feedback
- Supporting every programming language
- Treating public-test success as proof of correctness

## Acceptance criteria

- The zero-credential test suite passes on a clean checkout.
- A run cannot skip the plan-approval state.
- Disallowed commands and repository escapes are rejected.
- Context results contain selection reasons.
- Added test-skip markers fail the integrity check.
- The API rejects repository paths outside the configured root.

