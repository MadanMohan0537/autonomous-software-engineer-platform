"""Scorers that estimate P(a human approves this trajectory).

Three rungs of one ladder:

1. `HeuristicScorer`: fixed weights over the trajectory features. Zero labels needed.
2. `LogisticScorer`: the same features with weights fitted to human labels by gradient
   descent. A small, honest learned reward model that trains in milliseconds and
   refuses to train on fewer labels than it can learn from.
3. A LoRA trajectory classifier over the full text of trajectories (`lora.py`), which
   this release documents but does not train: it needs a few hundred reviewed labels.

Every scorer reads the same features, so the evaluation harness can compare rungs.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from ase.feedback.features import FEATURE_NAMES, TrajectoryFeatures

MIN_TRAINING_EXAMPLES = 30


class Scorer(Protocol):
    name: str

    def score(self, features: TrajectoryFeatures) -> float: ...


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


class HeuristicScorer:
    """The five-feature heuristic from the design doc, as a logit."""

    name = "heuristic"

    def score(self, features: TrajectoryFeatures) -> float:
        logit = (
            -1.0
            + 2.5 * features.tests_green
            + 1.5 * features.no_test_edits
            - 0.35 * features.diff_size
            + 1.0 * features.retrieval_overlap
            - 0.3 * max(features.iterations - 1.0, 0.0)
            - 1.0 * features.regressions
            - 3.0 * features.gave_up
        )
        return round(_sigmoid(logit), 4)


class LogisticScorer:
    name = "logistic"

    def __init__(
        self, weights: Sequence[float], bias: float, mean: Sequence[float], scale: Sequence[float]
    ) -> None:
        self.weights = np.asarray(weights, dtype=float)
        self.bias = float(bias)
        self.mean = np.asarray(mean, dtype=float)
        self.scale = np.asarray(scale, dtype=float)

    def score(self, features: TrajectoryFeatures) -> float:
        vector = (np.asarray(features.vector(), dtype=float) - self.mean) / self.scale
        return round(_sigmoid(float(vector @ self.weights + self.bias)), 4)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "features": FEATURE_NAMES,
                    "weights": self.weights.tolist(),
                    "bias": self.bias,
                    "mean": self.mean.tolist(),
                    "scale": self.scale.tolist(),
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> LogisticScorer:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["weights"], data["bias"], data["mean"], data["scale"])


class NotEnoughLabels(ValueError):
    pass


def train_logistic(
    examples: Sequence[tuple[TrajectoryFeatures, int]],
    epochs: int = 500,
    learning_rate: float = 0.1,
    l2: float = 0.01,
    min_examples: int = MIN_TRAINING_EXAMPLES,
) -> LogisticScorer:
    """Fit a logistic regression by gradient descent. Refuses tiny or one-sided datasets."""
    if len(examples) < min_examples:
        raise NotEnoughLabels(
            f"{len(examples)} labelled trajectories; need at least {min_examples}"
        )
    labels = np.asarray([label for _, label in examples], dtype=float)
    if labels.min() == labels.max():
        raise NotEnoughLabels("all labels are identical; nothing to learn")
    matrix = np.asarray([features.vector() for features, _ in examples], dtype=float)
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale == 0] = 1.0
    normalised = (matrix - mean) / scale
    weights = np.zeros(matrix.shape[1])
    bias = 0.0
    for _ in range(epochs):
        logits = normalised @ weights + bias
        predictions = 1.0 / (1.0 + np.exp(-logits))
        error = predictions - labels
        weights -= learning_rate * (normalised.T @ error / len(labels) + l2 * weights)
        bias -= learning_rate * float(error.mean())
    return LogisticScorer(weights.tolist(), bias, mean.tolist(), scale.tolist())


def rank_candidates(
    scorer: Scorer, candidates: Sequence[tuple[str, TrajectoryFeatures]]
) -> list[tuple[str, float]]:
    """Best-of-N: highest predicted approval first. Ties keep submission order."""
    scored = [(identifier, scorer.score(features)) for identifier, features in candidates]
    return sorted(scored, key=lambda item: -item[1])
