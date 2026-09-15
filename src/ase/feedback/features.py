"""Features of a trajectory that a scorer can read without executing anything.

These are the five signals from the design doc plus a few cheap extras. They are
deliberately simple: with only a few hundred labels, anything richer overfits to surface
shape, and the diff-scan features stay in even once a learned model exists so a reward
model can never learn to like patches that skip tests.
"""

from __future__ import annotations

from pydantic import BaseModel

from ase.contracts import AgentRun, Patch, Step, TestReport

FEATURE_NAMES = [
    "tests_green",
    "no_test_edits",
    "diff_size",
    "retrieval_overlap",
    "iterations",
    "regressions",
    "files_changed",
    "tool_calls",
    "gave_up",
]


class TrajectoryFeatures(BaseModel):
    tests_green: float
    no_test_edits: float
    diff_size: float  # log-scaled added+removed lines
    retrieval_overlap: float  # touched files that were in the retrieved context
    iterations: float
    regressions: float
    files_changed: float
    tool_calls: float
    gave_up: float

    def vector(self) -> list[float]:
        return [float(getattr(self, name)) for name in FEATURE_NAMES]


def _diff_lines(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )


def extract_features(
    run: AgentRun,
    steps: list[Step],
    patch: Patch | None = None,
    report: TestReport | None = None,
) -> TrajectoryFeatures:
    patch = patch or run.patch
    report = report or run.test_report
    context_files = {item.path for item in run.context[:10]}
    touched = [path for path in (patch.files if patch else [])]
    overlap = (
        len([path for path in touched if path in context_files]) / len(touched) if touched else 0.0
    )
    import math

    return TrajectoryFeatures(
        tests_green=1.0 if report is not None and report.green else 0.0,
        no_test_edits=0.0 if patch is not None and patch.touched_tests else 1.0,
        diff_size=math.log1p(_diff_lines(patch.diff) if patch else 0),
        retrieval_overlap=overlap,
        iterations=float(run.iteration),
        regressions=float(len(report.regressions)) if report is not None else 0.0,
        files_changed=float(len(touched)),
        tool_calls=float(sum(len(step.tool_calls) for step in steps)),
        gave_up=1.0 if run.outcome in {"gave_up", "failed"} else 0.0,
    )
