from ase.contracts import AgentRun, Issue
from ase.evaluation import score_trajectory


def test_trajectory_score_rewards_complete_evidence() -> None:
    run = AgentRun(issue=Issue(repository="demo", number=1, title="Bug"))
    for kind in ("run_created", "context_retrieved", "plan_proposed"):
        run.record(kind)
    score = score_trajectory(run)
    assert score.evidence_completeness == 1
    assert score.state_validity == 1
    assert score.total == 0.7
