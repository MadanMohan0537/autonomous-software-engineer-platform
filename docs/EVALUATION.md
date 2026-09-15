# Evaluation Strategy

## What is evaluated

The platform evaluates two related artifacts:

1. The patch: correctness, regression risk, scope, test strength, and policy compliance.
2. The trajectory: context selection, plan quality, state validity, evidence completeness,
   command efficiency, and human corrections.

## Layers

### Local deterministic suite

Runs without model credentials and protects contracts, state transitions, path
containment, policy behavior, retrieval explanations, and API boundaries.

### Controlled repository tasks

Each task contains issue text, a repository revision, expected affected files, hidden
tests, and required checks. The sample in `benchmarks/tasks` defines the schema direction.

### External repository benchmarks

SWE-bench adapters should run in its prescribed containerized harness. Results must
record dataset version, instance IDs, model configuration, attempt budget, environment,
and resolved count. HumanEval may supplement function-generation measurements but is
not a repository-level product metric.

### Shadow tasks

Recent internal tasks can be replayed with the accepted patch hidden. A human reviewer
must assess whether the candidate is correct, minimal, and maintainable.

## Core metrics

- Issue resolution and hidden-test pass rate
- Mutation score delta
- Unnecessary-file change rate
- Test weakening detection rate
- Unsafe command interception rate
- Repeated-run agreement
- Human plan approval and draft-PR acceptance
- Reviewer correction distance
- Time and cost per accepted patch

## Reporting rules

- Preserve case-level failures; never publish only an aggregate score.
- Separate attempted, completed, and resolved tasks.
- Report confidence intervals where sample size permits.
- Never compare runs with different task sets without labeling the difference.
- Keep evaluation data separate from training and prompt-development data.

