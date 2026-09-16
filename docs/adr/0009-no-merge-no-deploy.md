# ADR 0009: the platform has no merge or deploy authority

**Status:** accepted

## Context

A CI/CD agent that can merge and deploy is the shortest path from a model mistake to a
production incident. The project's principle is that consequential actions stay behind
human approval, and the code should make the principle structural rather than
procedural.

## Decision

`Policy.allow_merge` and `Policy.allow_deploy` default to `false`, `PolicyEngine.
authority()` is the single place they are checked, and the code paths that would need
them do not exist: the GitHub adapter can create branches, commits and *draft* pull
requests only; `CiWatcher` labels, re-runs, re-enters tasks or escalates; `Canary
Controller.deploy` raises `AuthorityDenied` against the default policy. The canary
module is a decision engine and simulator.

## Consequences

- The autonomous loop ends at a draft pull request with a human reviewer.
- Progressive delivery is taught by simulation (`ase canary simulate`), not practised.
- Enabling merge or deploy would be a policy change *and* new code, both reviewable.
