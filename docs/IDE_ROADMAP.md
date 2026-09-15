# Advanced IDE Roadmap

The next features are ranked by product value, technical differentiation, and their
ability to produce measurable evaluation evidence.

## 1. Evidence-aware code navigation

Overlay every retrieved file and symbol with the exact reason it entered the agent's
context: issue-term match, graph distance, stack-trace reference, ownership history, or
prior failure. Reviewers could remove irrelevant context before approving a plan.

**Measure:** context precision, reviewer removals, resolution rate after context edits.

## 2. Plan-to-diff conformance

Represent an approved plan as file and symbol constraints. As the patch changes, show
which hunks satisfy a planned step, which are unplanned, and which planned changes are
still missing. Block the draft PR when unexplained changes exceed policy.

**Measure:** unnecessary-file rate, unplanned-hunk rate, reviewer correction distance.

## 3. Counterexample laboratory

Before a patch can pass, generate boundary cases from the issue, changed branches, type
constraints, and historical failures. Run them in an isolated shadow suite and expose
which counterexample killed each candidate patch.

**Measure:** hidden-regression discovery and mutation score improvement.

## 4. Multi-agent disagreement review

Use independent planner, implementer, test critic, and security critic roles. Do not
average their answers. Show disagreements as review items and require evidence to
resolve them.

**Measure:** critic precision, defects caught before PR, cost per accepted finding.

## 5. Temporal code graph

Extend the code graph with commit history, co-change relationships, incident links, and
test flakiness. Let reviewers compare today's dependency graph with the graph at the
last known-good release.

**Measure:** root-cause localization time and historical-context usefulness.

## 6. Patch risk simulator

Estimate affected APIs, tests, owners, downstream packages, and deployment blast radius
before executing the patch. This is a deterministic impact analysis, not a fabricated
probability of failure.

**Measure:** affected-component recall and escaped dependency regressions.

## 7. Reviewer preference memory

Learn repository-specific review conventions from structured feedback: acceptable
abstractions, naming, compatibility expectations, test style, and prohibited shortcuts.
Keep preferences inspectable, versioned, and removable rather than hiding them in a
prompt.

**Measure:** repeated correction rate and preference retrieval precision.

## 8. Reproducibility capsule

Attach a content-addressed capsule to every draft PR containing the base revision,
policy version, model configuration, context manifest, commands, patch, test artifacts,
and environment digest. A reviewer can replay it without trusting the original worker.

**Measure:** replay success rate and evidence completeness.

## 9. Cost and latency budget planner

Before a run, estimate the number of retrieval, model, execution, and evaluation steps.
Allow the reviewer to choose a bounded budget and show when the agent requests more.

**Measure:** accepted patches per dollar and budget-overrun rate.

## 10. Product-to-code lineage

Connect an approved ProdMind decision to its engineering issue, plan, patch, PR,
deployment, outcome monitor, and realized benefit. Preserve the evidence IDs without
giving either system authority to invent business value.

**Measure:** decisions with complete delivery lineage and time from approval to observed
outcome.

## Recommended execution order

1. Plan-to-diff conformance
2. Evidence-aware navigation
3. Reproducibility capsules
4. Counterexample laboratory
5. Patch risk simulator
6. Reviewer preference memory
7. Product-to-code lineage
8. Multi-agent disagreement review
9. Temporal code graph
10. Cost and latency budget planner

The first three should be implemented before adding more autonomous authority. They
improve control, auditability, and reviewer trust without depending on a stronger model.

