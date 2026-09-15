"""Join runs with human reviews into a labelled dataset, and measure scorer agreement.

Only human decisions are labels. Approved or merged is positive; changes requested or
closed without merge is negative; comments alone and CI results are not labels.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from ase.contracts import AgentRun, ReviewDecision
from ase.feedback.features import TrajectoryFeatures, extract_features
from ase.feedback.reward import Scorer
from ase.store import PlatformStore

POSITIVE = {ReviewDecision.APPROVED, ReviewDecision.MERGED}
NEGATIVE = {ReviewDecision.CHANGES_REQUESTED, ReviewDecision.CLOSED}
MACHINE_REVIEWERS = {"github-actions", "ci"}


class LabeledTrajectory(BaseModel):
    run_id: str
    pr_number: int | None
    label: int
    decision: str
    features: TrajectoryFeatures
    config_name: str = "default"


class AgreementReport(BaseModel):
    scorer: str
    count: int
    agreement: float
    true_positive: int = 0
    true_negative: int = 0
    false_positive: int = 0
    false_negative: int = 0
    scores: list[float] = Field(default_factory=list)


def human_label(store: PlatformStore, run: AgentRun) -> tuple[int, str] | None:
    """The latest human decision wins; merged beats everything."""
    reviews = [r for r in store.reviews(run.id) if r.reviewer not in MACHINE_REVIEWERS]
    if any(r.decision == ReviewDecision.MERGED for r in reviews):
        return 1, ReviewDecision.MERGED.value
    decisive = [r for r in reviews if r.decision in POSITIVE | NEGATIVE]
    if not decisive:
        return None
    latest = max(decisive, key=lambda r: r.at)
    return (1 if latest.decision in POSITIVE else 0), latest.decision.value


def build_dataset(store: PlatformStore, runs: list[AgentRun]) -> list[LabeledTrajectory]:
    dataset: list[LabeledTrajectory] = []
    for run in runs:
        labelled = human_label(store, run)
        if labelled is None:
            continue
        label, decision = labelled
        features = extract_features(run, store.steps(run.id))
        dataset.append(
            LabeledTrajectory(
                run_id=run.id,
                pr_number=run.pr_number,
                label=label,
                decision=decision,
                features=features,
                config_name=run.config.name,
            )
        )
    return dataset


def export_dataset(dataset: list[LabeledTrajectory], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in dataset:
            handle.write(json.dumps(item.model_dump(mode="json"), sort_keys=True) + "\n")
    return len(dataset)


def load_dataset(path: Path) -> list[LabeledTrajectory]:
    return [
        LabeledTrajectory.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def agreement(
    scorer: Scorer, dataset: list[LabeledTrajectory], threshold: float = 0.5
) -> AgreementReport:
    report = AgreementReport(scorer=scorer.name, count=len(dataset), agreement=0.0)
    for item in dataset:
        score = scorer.score(item.features)
        report.scores.append(score)
        predicted = 1 if score >= threshold else 0
        if predicted == 1 and item.label == 1:
            report.true_positive += 1
        elif predicted == 0 and item.label == 0:
            report.true_negative += 1
        elif predicted == 1:
            report.false_positive += 1
        else:
            report.false_negative += 1
    if dataset:
        report.agreement = round((report.true_positive + report.true_negative) / len(dataset), 4)
    return report
