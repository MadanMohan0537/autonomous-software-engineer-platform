# ADR 0005: a generated test is kept only if it kills a mutant

**Status:** accepted

## Context

Coverage-guided test generation raises the coverage number by construction: any test that
executes uncovered lines and passes "covers" them. A test that asserts nothing meaningful
is worse than no test, because it hides the gap it was meant to close.

## Decision

`ase.testing.generate.TestGenerator` accepts a candidate test only if it passes on the
current code **and** kills at least one mutant of the file it targets, using the built-in
mutator from `ase.testing.mutation`. Rejected candidates are recorded with the reason
(`failed`, `tautology`, `rejected`) so the model's hit rate is measurable.

## Consequences

- Generation is slower (each candidate runs against a mutant set) and is budgeted.
- Coverage is reported but is not the acceptance criterion; mutation kills are.
- The same mutator serves module 3's weak-spot analysis, so "weak spot" and "test that
  fixes it" are measured by the same instrument.
