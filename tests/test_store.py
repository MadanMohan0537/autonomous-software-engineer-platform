from pathlib import Path

import pytest

from ase.contracts import (
    AgentRun,
    EvalResult,
    Issue,
    Patch,
    Review,
    ReviewDecision,
    Step,
    TestReport,
    ToolCall,
)
from ase.store import MemoryRunStore, SqliteRunStore


def _run() -> AgentRun:
    return AgentRun(issue=Issue(repository="demo", number=1, title="Bug"))


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> MemoryRunStore | SqliteRunStore:
    if request.param == "memory":
        return MemoryRunStore()
    return SqliteRunStore(tmp_path / "data" / "runs.sqlite3")


def test_round_trips_runs_and_traces(store: MemoryRunStore | SqliteRunStore) -> None:
    run = _run()
    store.save(run)
    assert store.get(run.id) is not None
    assert [item.id for item in store.list()] == [run.id]

    step = Step(run_id=run.id, index=1, node="edit", tool_calls=[ToolCall(name="read_file")])
    store.add_step(step)
    store.add_patch(Patch.from_diff(run.id, 1, "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"))
    store.add_test_report(TestReport(run_id=run.id, step_index=1, fail_to_pass={"t": True}))
    store.add_review(Review(run_id=run.id, pr_number=3, decision=ReviewDecision.APPROVED))
    store.add_eval_result(
        EvalResult(suite="s", task_id="t1", run_id=run.id, config_name="c", resolved=True)
    )

    assert store.steps(run.id)[0].tool_calls[0].name == "read_file"
    assert store.patches(run.id)[0].files == ["x.py"]
    assert store.test_reports(run.id)[0].green
    assert store.reviews(run.id)[0].decision == ReviewDecision.APPROVED
    assert store.reviews()[0].pr_number == 3
    assert store.eval_results("s")[0].resolved
    assert store.eval_results()[0].task_id == "t1"
    assert store.get("missing") is None


def test_sqlite_export_and_replace(tmp_path: Path) -> None:
    store = SqliteRunStore(tmp_path / "runs.sqlite3")
    run = _run()
    store.save(run)
    run.record("run_created")
    store.save(run)
    assert len(store.list()) == 1
    assert len(store.get(run.id).events) == 1  # type: ignore[union-attr]
    destination = tmp_path / "out" / "runs.jsonl"
    assert store.export_jsonl("agent_runs", destination) == 1
    assert run.id in destination.read_text(encoding="utf-8")
    with pytest.raises(KeyError):
        store.export_jsonl("nope", destination)
    store.close()


def test_patch_metadata_and_budget() -> None:
    diff = "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@\n+x\n"
    patch = Patch.from_diff("run", 1, diff)
    assert patch.touched_tests and patch.files == ["tests/test_x.py"] and patch.sha256
    run = _run()
    run.add_step(Step(run_id=run.id, index=1, node="plan", prompt_tokens=10, cost_usd=0.5))
    assert run.steps == 1 and run.prompt_tokens == 10 and run.cost_usd == 0.5
    assert run.budget_exceeded(elapsed_seconds=0) is None
    run.iteration = run.config.budget.max_iterations
    assert "iterations" in (run.budget_exceeded(0) or "")
    run.iteration = 0
    run.cost_usd = 99
    assert "cost" in (run.budget_exceeded(0) or "")
    run.cost_usd = 0
    assert "elapsed" in (run.budget_exceeded(10_000) or "")
    run.prompt_tokens = 10**7
    assert "tokens" in (run.budget_exceeded(0) or "")


def test_report_green_and_regressions() -> None:
    report = TestReport(run_id="r", fail_to_pass={"a": True}, pass_to_pass={"b": False}, failed=1)
    assert not report.green
    assert report.regressions == ["b"]
