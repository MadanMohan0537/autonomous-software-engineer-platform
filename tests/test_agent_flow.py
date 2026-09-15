"""End-to-end agent workflow on the fixture repository with a scripted model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

from ase.agent import AgentNodes, AgentServices, RejectAll, SequentialRunner, initial_state
from ase.agent.delivery import DryRunDelivery, GitHubDelivery, branch_name, sanitize_title
from ase.agent.runner import CallbackGate
from ase.agent.state import AgentState
from ase.contracts import Budget, Issue, RunConfig, RunState, Task
from ase.github import GitHubApi
from ase.llm import Completion, ScriptedClient, text_completion, tool_completion
from ase.policy import Policy, PolicyEngine
from ase.repo_intelligence import KnowledgeIndex
from ase.sandbox import LocalSandbox
from ase.store import MemoryRunStore
from ase.workspace import WorkspaceManager

ISSUE = Issue(
    repository="owner/pricing",
    number=7,
    title="refund_amount rejects a zero adjustment",
    body="refund_amount(20, 0) raises ValueError but zero should be allowed.",
)

PLAN = {
    "summary": "Allow a zero adjustment in refund_amount",
    "steps": [
        {
            "description": "Change the guard from <= 0 to < 0",
            "files": ["pricing/calc.py"],
            "risk": "low",
        }
    ],
    "assumptions": ["Zero is a valid adjustment"],
    "reproduction_test": {
        "path": "tests/test_reproduce_zero.py",
        "code": (
            "from pricing.calc import refund_amount\n\n\n"
            "def test_zero_adjustment():\n    assert refund_amount(10, 0) == 10\n"
        ),
    },
}

EDIT = {"path": "pricing/calc.py", "search": "adjustment <= 0", "replace": "adjustment < 0"}


def plan_completion(plan: dict[str, Any] | None = None) -> Completion:
    return text_completion("```json\n" + json.dumps(plan or PLAN) + "\n```", tokens=400)


def build(
    fixture_repo: Path,
    tmp_path: Path,
    script: list[Any],
    policy: Policy | None = None,
    delivery: Any = None,
    cleanup: bool = True,
) -> tuple[AgentServices, MemoryRunStore, ScriptedClient]:
    store = MemoryRunStore()
    llm = ScriptedClient(script)
    engine = PolicyEngine(policy or Policy())
    indexes: dict[str, KnowledgeIndex] = {}

    def index_loader(repository: Path, sha: str) -> KnowledgeIndex:
        if sha not in indexes:
            indexes[sha] = KnowledgeIndex.build(repository, sha)
        return indexes[sha]

    services = AgentServices(
        store=store,
        llm=llm,
        workspaces=WorkspaceManager(tmp_path / "ws"),
        policy=engine,
        sandbox_factory=lambda root: LocalSandbox(root, engine),
        index_loader=index_loader,
        delivery=delivery if delivery is not None else DryRunDelivery(tmp_path / "patches"),
        python=sys.executable,
        cleanup_workspaces=cleanup,
    )
    return services, store, llm


def start(
    services: AgentServices, fixture_repo: Path, config: RunConfig | None = None
) -> AgentState:
    from ase.contracts import AgentRun

    config = config or RunConfig(budget=Budget(max_iterations=3))
    task = Task(issue=ISSUE)
    run = AgentRun(issue=ISSUE, task=task, config=config)
    services.store.save(run)
    return initial_state(run.id, str(fixture_repo), task, config)


def test_issue_becomes_a_draft_pr(fixture_repo: Path, tmp_path: Path) -> None:
    script = [
        plan_completion(),
        tool_completion("read_file", {"path": "pricing/calc.py"}, text="Reading the guard."),
        tool_completion("edit_file", EDIT),
        tool_completion(
            "run_command",
            {"argv": ["python", "-m", "pytest", "-q", "tests/test_reproduce_zero.py"]},
        ),
        text_completion("Relaxed the guard so zero adjustments are accepted."),
    ]
    services, store, llm = build(fixture_repo, tmp_path, script)
    state = start(services, fixture_repo)
    final = SequentialRunner(AgentNodes(services)).run(state)

    assert final["outcome"] == "submitted"
    run = store.get(state["run_id"])
    assert run is not None and run.state == RunState.COMPLETED
    assert run.pr_url and run.pr_url.startswith("file://") and run.pr_number == 1
    assert run.task is not None and run.task.fail_to_pass == [
        "tests/test_reproduce_zero.py::test_zero_adjustment"
    ]
    assert run.patch is not None and run.patch.files == [
        "pricing/calc.py",
        "tests/test_reproduce_zero.py",
    ]
    assert run.test_report is not None and run.test_report.green
    assert run.evaluation is not None and run.evaluation.passed
    assert run.iteration == 1 and run.steps == 5 and run.cost_usd > 0
    assert [step.node for step in store.steps(run.id)] == [
        "plan",
        "implement",
        "implement",
        "implement",
        "implement",
    ]
    assert store.patches(run.id)[0].sha256 == run.patch.sha256
    assert store.test_reports(run.id)
    kinds = [event.kind for event in run.events]
    assert kinds[:6] == [
        "intake",
        "context_retrieved",
        "plan_proposed",
        "plan_reviewed",
        "workspace_created",
        "patch_produced",
    ]
    assert "draft_pr_opened" in kinds
    assert not (tmp_path / "ws").exists() or not any((tmp_path / "ws").iterdir())
    # The coder saw the approved plan, the repo map and the reproduction test id.
    coder_request = llm.requests[1].last_text
    assert "Approved plan" in coder_request and "tests/test_reproduce_zero.py" in coder_request
    body = list((tmp_path / "patches").iterdir())[0].read_text(encoding="utf-8")
    assert "Fail-to-pass now passing" in body and "reproduction test installed" in body
    assert "human reviewer" in body


def test_repeated_patch_gives_up_and_model_cannot_promote_red(
    fixture_repo: Path, tmp_path: Path
) -> None:
    write_test = {
        "argv": [
            "python",
            "-c",
            "open('tests/test_calc.py','a').write('\\n# touched by agent\\n')",
        ]
    }
    script = [
        plan_completion(),
        tool_completion("run_command", write_test),
        text_completion("Adjusted the test expectations."),
        text_completion(json.dumps({"decision": "submit", "reason": "looks fine"})),
        text_completion("Nothing else to do."),
    ]
    services, store, _ = build(fixture_repo, tmp_path, script)
    state = start(services, fixture_repo, RunConfig(budget=Budget(max_iterations=4)))
    final = SequentialRunner(AgentNodes(services)).run(state)
    assert final["outcome"] == "gave_up"
    run = store.get(state["run_id"])
    assert run is not None and run.state == RunState.FAILED and run.outcome == "gave_up"
    assert run.evaluation is not None and not run.evaluation.passed
    assert any("test files" in violation for violation in run.evaluation.policy_violations)
    reflections = [event for event in run.events if event.kind == "reflected"]
    assert reflections[0].payload["decision"] == "retry"
    assert "identical patches" in reflections[-1].payload["reason"]
    assert "test files" in reflections[0].payload["focus"]


def test_budget_exhaustion_stops_the_loop(fixture_repo: Path, tmp_path: Path) -> None:
    script = [plan_completion(), text_completion("I could not find the bug.")]
    services, store, _ = build(fixture_repo, tmp_path, script)
    state = start(services, fixture_repo, RunConfig(budget=Budget(max_iterations=1)))
    final = SequentialRunner(AgentNodes(services)).run(state)
    assert final["outcome"] == "gave_up" and "iterations" in final["outcome_reason"]
    assert store.get(state["run_id"]).state == RunState.FAILED  # type: ignore[union-attr]


def test_plan_and_pr_rejections(fixture_repo: Path, tmp_path: Path) -> None:
    services, store, _ = build(fixture_repo, tmp_path, [plan_completion()])
    state = start(services, fixture_repo)
    final = SequentialRunner(AgentNodes(services), RejectAll()).run(state)
    assert final["outcome"] == "rejected" and final.get("workspace") is None
    run = store.get(state["run_id"])
    assert run is not None and run.state == RunState.FAILED and run.plan is not None
    assert run.plan.approval.value == "rejected"

    script = [plan_completion(), tool_completion("edit_file", EDIT), text_completion("done")]
    services, store, _ = build(fixture_repo, tmp_path / "second", script)
    state = start(services, fixture_repo)
    gate = CallbackGate(lambda kind, _: {"approved": kind == "plan", "reason": "no PRs today"})
    final = SequentialRunner(AgentNodes(services), gate).run(state)
    assert final["outcome"] == "rejected" and final["outcome_reason"] == "no PRs today"
    assert store.get(state["run_id"]).state == RunState.FAILED  # type: ignore[union-attr]


def test_command_only_mode_and_dropped_reproduction(fixture_repo: Path, tmp_path: Path) -> None:
    passing_plan = dict(PLAN)
    passing_plan["reproduction_test"] = {
        "path": "tests/test_reproduce_pass.py",
        "code": "def test_already_passes():\n    assert True\n",
    }
    fix = {
        "argv": [
            "python",
            "-c",
            "p='pricing/calc.py'; s=open(p).read(); "
            "open(p,'w').write(s.replace('adjustment <= 0','adjustment < 0'))",
        ]
    }
    script = [
        plan_completion(passing_plan),
        tool_completion("run_command", fix),
        text_completion("fixed"),
    ]
    services, store, llm = build(fixture_repo, tmp_path, script)
    config = RunConfig(tool_mode="command_only", use_index=False, budget=Budget(max_iterations=2))
    state = start(services, fixture_repo, config)
    final = SequentialRunner(AgentNodes(services)).run(state)
    assert final["outcome"] == "submitted"
    assert [tool.name for tool in llm.requests[1].tools] == ["run_command"]
    assert any("dropped" in note for note in final["notes"])
    assert any("whole suite" in note for note in final["notes"])
    run = store.get(state["run_id"])
    assert run is not None and run.task is not None and run.task.fail_to_pass == []
    assert run.test_report is not None and run.test_report.total == 4


def test_invalid_reproduction_paths_are_rejected(fixture_repo: Path, tmp_path: Path) -> None:
    bad_plan = dict(PLAN)
    bad_plan["reproduction_test"] = {"path": "pricing/evil.py", "code": "def test_x():\n    pass\n"}
    script = [plan_completion(bad_plan), tool_completion("edit_file", EDIT), text_completion("ok")]
    services, _, _ = build(fixture_repo, tmp_path, script)
    final = SequentialRunner(AgentNodes(services)).run(start(services, fixture_repo))
    assert any("path rejected" in note for note in final["notes"])
    assert final["outcome"] == "submitted"

    unparsable = dict(PLAN)
    unparsable["reproduction_test"] = {"path": "tests/test_bad.py", "code": "def (:\n"}
    script = [
        plan_completion(unparsable),
        tool_completion("edit_file", EDIT),
        text_completion("ok"),
    ]
    services, _, _ = build(fixture_repo, tmp_path / "b", script)
    final = SequentialRunner(AgentNodes(services)).run(start(services, fixture_repo))
    assert any("does not parse" in note for note in final["notes"])


def test_github_delivery_publishes_branch_and_draft_pr(fixture_repo: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob1"})
        if path.endswith("/git/commits/base0"):
            return httpx.Response(200, json={"tree": {"sha": "tree0"}})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree1"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "commit1"})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if path == "/repos/owner/pricing":
            return httpx.Response(200, json={"default_branch": "develop"})
        if path.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://gh/pr/9", "number": 9})
        return httpx.Response(404, json={})

    api = GitHubApi(
        "token",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test"),
    )
    delivery = GitHubDelivery(api)
    (fixture_repo / "pricing" / "calc.py").write_text("x = 1\n", encoding="utf-8")
    published = delivery.publish(
        "owner/pricing",
        "base0",
        "ase/issue-7-abc",
        fixture_repo,
        ["pricing/calc.py", "gone.py"],
        "fix: zero adjustment",
        "[draft] #7: fix",
        "body",
    )
    assert (
        published.pr_url == "https://gh/pr/9"
        and published.pr_number == 9
        and published.commit_sha == "commit1"
    )
    methods = [(method, path.rsplit("/", 1)[-1]) for method, path, _ in calls]
    assert methods == [
        ("POST", "blobs"),
        ("GET", "base0"),
        ("POST", "trees"),
        ("POST", "commits"),
        ("POST", "refs"),
        ("GET", "pricing"),
        ("POST", "pulls"),
    ]
    tree_entries = calls[2][2]["tree"]  # type: ignore[index]
    assert tree_entries[1] == {"path": "gone.py", "mode": "100644", "type": "blob", "sha": None}
    pull = calls[-1][2]
    assert pull == {  # type: ignore[comparison-overlap]
        "title": "[draft] #7: fix",
        "body": "body",
        "head": "ase/issue-7-abc",
        "base": "develop",
        "draft": True,
    }
    assert branch_name("run_abc", 7) == "ase/issue-7-abc"
    assert sanitize_title("  Fix   the\nthing " * 10, 7).startswith("[draft] #7: Fix the thing")
