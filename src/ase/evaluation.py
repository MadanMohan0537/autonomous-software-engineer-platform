"""Trajectory and patch scoring for local benchmark tasks."""

from __future__ import annotations

from dataclasses import dataclass

from ase.contracts import AgentRun


@dataclass(frozen=True)
class TrajectoryScore:
    evidence_completeness: float
    state_validity: float
    evaluation_passed: float

    @property
    def total(self) -> float:
        return round(
            0.4 * self.evidence_completeness
            + 0.3 * self.state_validity
            + 0.3 * self.evaluation_passed,
            4,
        )


def score_trajectory(run: AgentRun) -> TrajectoryScore:
    expected_events = {"run_created", "context_retrieved", "plan_proposed"}
    observed = {event.kind for event in run.events}
    completeness = len(expected_events & observed) / len(expected_events)
    ordered = [event.sequence for event in run.events]
    state_validity = 1.0 if ordered == list(range(1, len(ordered) + 1)) else 0.0
    passed = 1.0 if run.evaluation and run.evaluation.passed else 0.0
    return TrajectoryScore(completeness, state_validity, passed)
