import random
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ase.contracts import (
    AgentRun,
    ContextItem,
    Issue,
    Patch,
    Review,
    ReviewDecision,
    Step,
    TestReport,
    ToolCall,
)
from ase.feedback import (
    HeuristicScorer,
    LogisticScorer,
    NotEnoughLabels,
    ReviewSync,
    TrajectoryFeatures,
    agreement,
    build_dataset,
    export_dataset,
    extract_features,
    human_label,
    load_dataset,
    rank_candidates,
    train_logistic,
)
from ase.feedback.lora import DatasetTooSmall, plan_training, train, write_manifest
from ase.github import GitHubApi
from ase.store import MemoryRunStore


def _run(green: bool, touched_tests: bool = False, outcome: str = "submitted") -> AgentRun:
    run = AgentRun(issue=Issue(repository="o/r", number=1, title="t"), pr_number=5, outcome=outcome)
    run.context = [ContextItem(path="pkg/a.py", reason="r", score=1.0)]
    run.iteration = 2
    diff = "--- a/pkg/a.py\n+++ b/pkg/a.py\n@@\n-x\n+y\n+z\n"
    if touched_tests:
        diff += "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@\n+skip\n"
    run.patch = Patch.from_diff(run.id, 1, diff)
    run.test_report = TestReport(
        run_id=run.id,
        fail_to_pass={"t": green},
        pass_to_pass={"p": True, "q": green},
        failed=0 if green else 1,
    )
    return run


def test_features_and_heuristic_ordering() -> None:
    good = _run(True)
    bad = _run(False, touched_tests=True, outcome="gave_up")
    steps = [
        Step(run_id=good.id, index=1, node="implement", tool_calls=[ToolCall(name="edit_file")])
    ]
    f_good = extract_features(good, steps)
    f_bad = extract_features(bad, [])
    assert f_good.tests_green == 1 and f_good.no_test_edits == 1 and f_good.retrieval_overlap == 1.0
    assert f_good.tool_calls == 1 and f_good.diff_size > 0 and f_good.files_changed == 1
    assert (
        f_bad.tests_green == 0
        and f_bad.no_test_edits == 0
        and f_bad.regressions == 1
        and f_bad.gave_up == 1
    )
    scorer = HeuristicScorer()
    assert scorer.score(f_good) > 0.8 > 0.2 > scorer.score(f_bad)
    ranked = rank_candidates(scorer, [("bad", f_bad), ("good", f_good)])
    assert [name for name, _ in ranked] == ["good", "bad"]
    assert len(f_good.vector()) == 9


def _features(seed: int, positive: bool) -> TrajectoryFeatures:
    rng = random.Random(seed)
    return TrajectoryFeatures(
        tests_green=1.0 if positive else float(rng.random() < 0.3),
        no_test_edits=1.0 if positive or rng.random() < 0.5 else 0.0,
        diff_size=rng.uniform(1, 3) if positive else rng.uniform(2, 6),
        retrieval_overlap=rng.uniform(0.5, 1) if positive else rng.uniform(0, 0.7),
        iterations=rng.uniform(1, 3) if positive else rng.uniform(2, 6),
        regressions=0.0 if positive else float(rng.randint(0, 3)),
        files_changed=float(rng.randint(1, 3)),
        tool_calls=float(rng.randint(2, 20)),
        gave_up=0.0 if positive else float(rng.random() < 0.4),
    )


def test_logistic_scorer_learns_and_persists(tmp_path: Path) -> None:
    examples = [(_features(i, i % 2 == 0), 1 if i % 2 == 0 else 0) for i in range(80)]
    scorer = train_logistic(examples)
    correct = sum(1 for feats, label in examples if (scorer.score(feats) >= 0.5) == bool(label))
    assert correct / len(examples) > 0.85
    path = tmp_path / "model" / "logistic.json"
    scorer.save(path)
    loaded = LogisticScorer.load(path)
    assert loaded.score(examples[0][0]) == scorer.score(examples[0][0])
    with pytest.raises(NotEnoughLabels):
        train_logistic(examples[:5])
    with pytest.raises(NotEnoughLabels):
        train_logistic([(feats, 1) for feats, _ in examples])


def test_dataset_from_reviews_and_agreement(tmp_path: Path) -> None:
    store = MemoryRunStore()
    approved, rejected, unlabeled, machine = _run(True), _run(False), _run(True), _run(True)
    for run in (approved, rejected, unlabeled, machine):
        store.save(run)
    store.add_review(
        Review(run_id=approved.id, pr_number=5, decision=ReviewDecision.COMMENTED, reviewer="a")
    )
    store.add_review(
        Review(run_id=approved.id, pr_number=5, decision=ReviewDecision.APPROVED, reviewer="a")
    )
    store.add_review(
        Review(
            run_id=rejected.id, pr_number=5, decision=ReviewDecision.CHANGES_REQUESTED, reviewer="b"
        )
    )
    store.add_review(
        Review(
            run_id=machine.id,
            pr_number=5,
            decision=ReviewDecision.CHANGES_REQUESTED,
            reviewer="github-actions",
        )
    )
    assert human_label(store, unlabeled) is None and human_label(store, machine) is None
    assert human_label(store, approved) == (1, "approved")
    dataset = build_dataset(store, [approved, rejected, unlabeled, machine])
    assert [item.label for item in dataset] == [1, 0]
    path = tmp_path / "labels.jsonl"
    assert (
        export_dataset(dataset, path) == 2 and load_dataset(path)[1].decision == "changes_requested"
    )
    report = agreement(HeuristicScorer(), dataset)
    assert report.count == 2 and report.agreement == 1.0 and report.true_positive == 1
    with pytest.raises(DatasetTooSmall):
        plan_training(dataset)
    with pytest.raises(DatasetTooSmall):
        plan_training([dataset[0]] * 200 + [dataset[1]] * 10, min_labels=100)
    plan = plan_training(dataset * 100, min_labels=100)
    assert plan.balanced and plan.positives == 100
    manifest = write_manifest(plan, path, tmp_path / "manifest.json")
    assert manifest.exists()
    with pytest.raises(NotImplementedError):
        train(plan, path)


def test_review_sync_mirrors_github(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/reviews"):
            return httpx.Response(
                200,
                json=[
                    {
                        "state": "COMMENTED",
                        "user": {"login": "m"},
                        "body": "nit",
                        "submitted_at": "2026-09-15T10:00:00Z",
                    },
                    {
                        "state": "APPROVED",
                        "user": {"login": "m"},
                        "submitted_at": "2026-09-15T11:00:00Z",
                    },
                    {"state": "DISMISSED", "user": {"login": "x"}},
                ],
            )
        if request.url.path.endswith("/pulls/5"):
            return httpx.Response(
                200,
                json={
                    "state": "closed",
                    "merged_at": "2026-09-15T12:00:00Z",
                    "merged_by": {"login": "m"},
                },
            )
        return httpx.Response(404, json={})

    api = GitHubApi(
        "t",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test"),
    )
    store = MemoryRunStore()
    run = _run(True)
    store.save(run)
    sync = ReviewSync(api, store)
    added = sync.sync(run)
    assert [item.decision.value for item in added] == ["commented", "approved", "merged"]
    assert added[0].comments == ["nit"] and added[1].at == datetime(2026, 9, 15, 11, tzinfo=UTC)
    assert sync.sync(run) == []  # idempotent
    assert sync.sync_all([run, AgentRun(issue=run.issue)]) == 0
    assert human_label(store, run) == (1, "merged")
