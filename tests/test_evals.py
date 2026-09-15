import json
import sys
from pathlib import Path

import httpx

from ase.agent.service import AgentService
from ase.config import Settings
from ase.contracts import Budget, EvalResult, RunConfig
from ase.evals import (
    EvalRunner,
    EvalTask,
    GitHarvester,
    Grader,
    PredictionsFile,
    Suite,
    apply_patch,
    candidate_commits,
    harness_command,
    harvest_from_pull_requests,
    instance_to_task,
    load_instances,
    load_suite,
    load_suites,
    read_results,
    render_cases,
    render_table,
    summarize,
)
from ase.github import GitHubApi
from ase.llm import ScriptedClient, text_completion, tool_completion
from ase.sandbox import LocalSandbox
from ase.store import MemoryRunStore
from ase.workspace import WorkspaceManager
from tests.conftest import git
from tests.test_agent_flow import EDIT, plan_completion

FIX_DIFF = """--- a/pricing/calc.py
+++ b/pricing/calc.py
@@ -11,5 +11,5 @@
 def refund_amount(paid: float, adjustment: float) -> float:
     \"\"\"Refund the paid amount minus a non-negative adjustment.\"\"\"
-    if adjustment <= 0:
+    if adjustment < 0:
         raise ValueError("adjustment must be non-negative")
     return round(paid - adjustment, 2)
"""


def _commit_fix(fixture_repo: Path) -> str:
    source = fixture_repo / "pricing" / "calc.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace("adjustment <= 0", "adjustment < 0"),
        encoding="utf-8",
    )
    tests = fixture_repo / "tests" / "test_calc.py"
    tests.write_text(
        tests.read_text(encoding="utf-8")
        + "\n\ndef test_refund_zero_again():\n    assert refund_amount(5, 0) == 5\n",
        encoding="utf-8",
    )
    git(
        fixture_repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@e",
        "commit",
        "-qam",
        "fix: allow zero adjustment",
    )
    return git(fixture_repo, "rev-parse", "HEAD").strip()


def test_suite_files_round_trip_and_legacy_task(tmp_path: Path) -> None:
    suite = Suite(
        name="s", tasks=[EvalTask(id="a", repository="/r", title="t", fail_to_pass=["x::y"])]
    )
    suite.write(tmp_path / "suites" / "s.json")
    loaded = load_suites(tmp_path / "suites")[0]
    assert loaded.tasks[0].to_task(3).fail_to_pass == ["x::y"]
    hidden = EvalTask(
        id="h", repository="/r", title="t", fail_to_pass=["x::y"], test_patch={"t.py": "x"}
    )
    assert hidden.to_task().fail_to_pass == [] and hidden.to_task().metadata["hidden_tests"]
    assert loaded.tasks[0].to_task(3).metadata["eval_task"] == "a"
    legacy = Path(__file__).resolve().parents[1] / "benchmarks" / "tasks" / "demo-refund.json"
    demo = load_suite(legacy)
    assert demo.tasks[0].expected_files == ["refunds.py", "test_refunds.py"]
    (tmp_path / "bad.json").write_text("[1]", encoding="utf-8")
    try:
        load_suite(tmp_path / "bad.json")
    except ValueError as exc:
        assert "unrecognised" in str(exc)


def test_harvest_measures_fail_to_pass(fixture_repo: Path, tmp_path: Path) -> None:
    fix_sha = _commit_fix(fixture_repo)
    candidates = candidate_commits(fixture_repo)
    assert candidates and candidates[0][0] == fix_sha
    harvester = GitHarvester(
        WorkspaceManager(tmp_path / "ws"), lambda root: LocalSandbox(root), sys.executable
    )
    suite = harvester.harvest(fixture_repo, limit=3)
    assert len(suite.tasks) == 1
    task = suite.tasks[0]
    assert task.title == "fix: allow zero adjustment" and task.metadata["fix_sha"] == fix_sha
    assert set(task.fail_to_pass) == {
        "tests/test_calc.py::test_refund_zero_adjustment_is_allowed",
        "tests/test_calc.py::test_refund_zero_again",
    }
    assert "tests/test_calc.py::test_discount_basic" in task.pass_to_pass
    assert task.expected_files == ["pricing/calc.py"] and "tests/test_calc.py" in task.test_patch
    assert not any((tmp_path / "ws").iterdir())


def test_grader_applies_patch_to_clean_checkout(fixture_repo: Path, tmp_path: Path) -> None:
    _commit_fix(fixture_repo)
    harvester = GitHarvester(
        WorkspaceManager(tmp_path / "ws"), lambda root: LocalSandbox(root), sys.executable
    )
    task = harvester.harvest(fixture_repo, limit=1).tasks[0]
    grader = Grader(
        WorkspaceManager(tmp_path / "grade"), lambda root: LocalSandbox(root), sys.executable
    )
    good = grader.grade(task, FIX_DIFF, ["pricing/calc.py"])
    assert good.resolved and good.applied and good.expected_file_overlap == 1.0
    assert all(good.fail_to_pass.values())
    empty = grader.grade(task, "", [])
    assert not empty.applied and not empty.resolved and "did not apply" in empty.notes[0]
    remote = grader.grade(EvalTask(id="r", repository="owner/name", title="t"), FIX_DIFF)
    assert not remote.resolved and "not a local path" in remote.notes[0]
    assert not apply_patch(fixture_repo, "garbage")


def test_eval_runner_produces_results_and_tables(fixture_repo: Path, tmp_path: Path) -> None:
    _commit_fix(fixture_repo)
    harvester = GitHarvester(
        WorkspaceManager(tmp_path / "ws"), lambda root: LocalSandbox(root), sys.executable
    )
    suite = harvester.harvest(fixture_repo, limit=1)
    settings = Settings(data_dir=tmp_path / "data")
    llm = ScriptedClient(
        [plan_completion(), tool_completion("edit_file", EDIT), text_completion("done")]
    )
    service = AgentService(settings, fixture_repo, store=MemoryRunStore(), llm=llm)
    grader = Grader(
        WorkspaceManager(tmp_path / "grade"), lambda root: LocalSandbox(root), sys.executable
    )
    runner = EvalRunner(service, grader, tmp_path / "results")
    config = RunConfig(name="structured", budget=Budget(max_iterations=2))
    results = runner.run_suite(suite, config)
    assert len(results) == 1 and results[0].resolved and results[0].cost_usd > 0
    assert "submitted" in results[0].notes
    stored = service.store.eval_results(suite.name)
    assert stored[0].task_id == suite.tasks[0].id
    reread = read_results(tmp_path / "results")
    assert reread[0].run_id == results[0].run_id
    summaries = summarize(
        [
            *reread,
            EvalResult(
                suite="s", task_id="x", run_id="r", config_name="c", resolved=False, cost_usd=1
            ),
        ]
    )
    table = render_table(summaries)
    assert (
        "| own-repo-replay | structured | 1 | 1 | 100% |" in table
        and "| s | c | 1 | 0 | 0% |" in table
    )
    assert summaries[0].cost_per_resolved is None or summaries[0].cost_per_resolved >= 0
    assert "yes" in render_cases(reread)


def test_swebench_adapter(tmp_path: Path) -> None:
    instance = {
        "instance_id": "owner__repo-1",
        "repo": "owner/repo",
        "base_commit": "abc",
        "problem_statement": "Title line\nMore details",
        "FAIL_TO_PASS": json.dumps(["tests/test_a.py::test_x"]),
        "PASS_TO_PASS": ["tests/test_a.py::test_y"],
        "version": "1.0",
    }
    task = instance_to_task(instance)
    assert task.title == "Title line" and task.fail_to_pass == ["tests/test_a.py::test_x"]
    assert task.pass_to_pass == ["tests/test_a.py::test_y"] and task.base_sha == "abc"
    path = tmp_path / "lite.jsonl"
    path.write_text(
        json.dumps(instance) + "\n" + json.dumps({**instance, "instance_id": "b"}) + "\n",
        encoding="utf-8",
    )
    suite = load_instances(path, limit=1)
    assert suite.name == "swebench-lite" and len(suite.tasks) == 1
    (tmp_path / "one.json").write_text(json.dumps([instance]), encoding="utf-8")
    assert load_instances(tmp_path / "one.json").tasks[0].id == "owner__repo-1"
    assert instance_to_task({"instance_id": "z", "FAIL_TO_PASS": "not json"}).fail_to_pass == [
        "not json"
    ]
    predictions = PredictionsFile(path=tmp_path / "preds" / "p.jsonl")
    predictions.add("owner__repo-1", "ase-sonnet", "diff")
    written = predictions.write()
    assert json.loads(written.read_text(encoding="utf-8"))["model_patch"] == "diff"
    assert harness_command(written)[2] == "swebench.harness.run_evaluation"


def test_harvest_from_pull_requests_uses_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {"number": 1, "merged_at": None, "title": "unmerged"},
                    {
                        "number": 2,
                        "merged_at": "x",
                        "title": "fix bug",
                        "body": "b",
                        "base": {"sha": "s"},
                        "merge_commit_sha": "m",
                    },
                    {"number": 3, "merged_at": "x", "title": "docs only", "base": {"sha": "s"}},
                ],
            )
        if request.url.path.endswith("/pulls/2/files"):
            return httpx.Response(
                200, json=[{"filename": "pkg/a.py"}, {"filename": "tests/test_a.py"}]
            )
        if request.url.path.endswith("/pulls/3/files"):
            return httpx.Response(200, json=[{"filename": "README.md"}])
        return httpx.Response(404, json={})

    api = GitHubApi(
        "t",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test"),
    )
    suite = harvest_from_pull_requests(api, "owner/repo", limit=5)
    assert [task.id for task in suite.tasks] == ["pr-2"]
    assert suite.tasks[0].expected_files == ["pkg/a.py"] and suite.tasks[0].base_sha == "s"
