from pathlib import Path

from ase.feedback import FeedbackDecision, FeedbackStore, ReviewFeedback


def test_feedback_round_trip(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path / "feedback.db")
    item = ReviewFeedback(
        run_id="run_1",
        stage="patch",
        decision=FeedbackDecision.CHANGES_REQUESTED,
        reason_codes=["missing_edge_case"],
    )
    assert store.append(item) == 1
    assert store.for_run("run_1") == [item]
