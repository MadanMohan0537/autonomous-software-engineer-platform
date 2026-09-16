# ADR 0007: reward modelling is a ladder of rungs gated on labels

**Status:** accepted

## Context

"Feedback trains a reward model" is the headline of module 6 and the easiest thing to
overclaim. A learned model trained on a few dozen labels overfits to surface features;
publishing it as a reward model would be dishonest and, worse, would be used.

## Decision

Three scorers implement one `Scorer` protocol over the same `TrajectoryFeatures`:

1. `HeuristicScorer`: fixed weights, needs zero labels, is the baseline.
2. `LogisticScorer`: weights fitted to human labels; `train_logistic` refuses datasets
   smaller than `MIN_TRAINING_EXAMPLES` or with one label class.
3. The LoRA trajectory classifier: `ase.feedback.lora` validates the dataset against
   `MIN_HUMAN_LABELS` (200), writes a training manifest, and stops. The training loop is
   outside this package.

`agreement` measures every rung against held-out human labels, and the diff-scan
features (tests touched, reproduction test intact) stay in the feature set for every
rung so a learned model cannot prefer patches that skip tests.

## Consequences

- The platform can rank candidates from day one with the heuristic rung.
- Nobody can claim a trained reward model without 200 reviewed labels on disk.
- Moving up a rung is an evaluation result (agreement improves), not a code change.
