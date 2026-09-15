"""The third rung: a LoRA trajectory classifier. Documented, gated, not yet trained.

The platform does not claim reward-model training until a sufficiently large and
reviewed dataset exists (docs/PRD.md). This module holds the recipe and the gate: it
validates the dataset, writes the training manifest, and stops. The training loop
itself (transformers + peft on a rented GPU) is deliberately outside this package's
dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from ase.feedback.dataset import LabeledTrajectory

MIN_HUMAN_LABELS = 200


class LoraTrainingPlan(BaseModel):
    base_model: str = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    rank: int = Field(default=16, ge=1)
    alpha: int = Field(default=32, ge=1)
    dropout: float = Field(default=0.05, ge=0, le=1)
    epochs: int = Field(default=3, ge=1)
    learning_rate: float = Field(default=2e-4, gt=0)
    max_length: int = Field(default=8192, ge=512)
    objective: str = "yes/no next-token classification over the serialised trajectory"
    positives: int = 0
    negatives: int = 0

    @property
    def balanced(self) -> bool:
        smaller = min(self.positives, self.negatives)
        return smaller >= 0.2 * (self.positives + self.negatives)


class DatasetTooSmall(ValueError):
    pass


def plan_training(
    dataset: list[LabeledTrajectory], min_labels: int = MIN_HUMAN_LABELS
) -> LoraTrainingPlan:
    positives = sum(1 for item in dataset if item.label == 1)
    negatives = len(dataset) - positives
    if len(dataset) < min_labels:
        raise DatasetTooSmall(
            f"{len(dataset)} human-labelled trajectories; a LoRA reward model needs at least "
            f"{min_labels}. Keep reviewing draft PRs and use the heuristic or logistic scorer "
            "meanwhile."
        )
    plan = LoraTrainingPlan(positives=positives, negatives=negatives)
    if not plan.balanced:
        raise DatasetTooSmall(
            f"labels are one-sided ({positives} positive / {negatives} negative); "
            "a classifier would learn the prior, not the reviews"
        )
    return plan


def write_manifest(plan: LoraTrainingPlan, dataset_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"plan": plan.model_dump(), "dataset": str(dataset_path)}, indent=2),
        encoding="utf-8",
    )
    return destination


def train(plan: LoraTrainingPlan, dataset_path: Path) -> None:
    raise NotImplementedError(
        "LoRA training is not part of this release. With the manifest written by "
        "`write_manifest`, run a transformers + peft fine-tune of "
        f"{plan.base_model} as a yes/no classifier over serialised trajectories; the "
        "logistic scorer remains the deployed model until an evaluation shows the LoRA "
        "model agrees with reviewers more often on held-out PRs."
    )
