import json
from typing import Any

import httpx
import pytest

from ase.ci import (
    ActionsClient,
    CanaryController,
    CiWatcher,
    FailureKind,
    FailureLog,
    Slo,
    Triage,
    Window,
    classify_by_rules,
    scrub_secrets,
    trim_log,
)
from ase.ci.actions import Job, WorkflowRun
from ase.ci.canary import AuthorityDenied
from ase.contracts import AgentRun, Issue, ReviewDecision, TaskSource
from ase.github import GitHubApi
from ase.llm import ScriptedClient, text_completion
from ase.policy import Policy, PolicyEngine
from ase.store import MemoryRunStore


def test_scrub_and_trim_logs() -> None:
    text = (
        "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    )
    scrubbed, count = scrub_secrets(text)
    assert "ghp_" not in scrubbed and "abcdefghijklmnop" not in scrubbed and count >= 2
    lines = [f"line {i}" for i in range(400)]
    lines[50] = "Traceback (most recent call last):"
    excerpt, truncated = trim_log("\n".join(lines), window=5, tail_lines=100)
    assert truncated and excerpt.splitlines()[0] == "Traceback (most recent call last):"
    assert "lines omitted" in excerpt and excerpt.splitlines()[-1] == "line 399"
    short, truncated = trim_log("a\nb")
    assert short == "a\nb" and not truncated


def test_rule_classifier_covers_common_shapes() -> None:
    assert classify_by_rules("ECONNRESET while fetching").kind == FailureKind.FLAKE  # type: ignore[union-attr]
    assert classify_by_rules("ModuleNotFoundError: No module named x").kind == FailureKind.ENV  # type: ignore[union-attr]
    assert classify_by_rules("ruff check .\nE501 line too long").kind == FailureKind.LINT  # type: ignore[union-attr]
    assert (
        classify_by_rules("FAILED tests/test_x.py::test_a - AssertionError").kind
        == FailureKind.TEST_REGRESSION
    )  # type: ignore[union-attr]
    assert classify_by_rules("all good") is None


def _log(excerpt: str) -> FailureLog:
    run = WorkflowRun(
        id=1, name="CI", status="completed", conclusion="failure", html_url="u", head_sha="h"
    )
    return FailureLog(run=run, job=Job(id=2, name="verify", failed_step="pytest"), excerpt=excerpt)


def test_triage_uses_model_only_when_rules_miss() -> None:
    llm = ScriptedClient(
        [text_completion(json.dumps({"kind": "flake", "reason": "runner died", "confidence": 0.9}))]
    )
    triage = Triage(llm)
    assert triage.classify(_log("FAILED tests/test_a.py")).source == "rules"
    modelled = triage.classify(_log("something odd happened"))
    assert (
        modelled.kind == FailureKind.FLAKE
        and modelled.source == "model"
        and modelled.confidence == 0.9
    )
    assert Triage().classify(_log("mystery")).kind == FailureKind.UNKNOWN
    garbage = Triage(ScriptedClient([text_completion("not json")])).classify(_log("mystery"))
    assert garbage.kind == FailureKind.UNKNOWN
    weird = Triage(
        ScriptedClient([text_completion('{"kind": "banana", "confidence": 7}')])
    ).classify(_log("m"))
    assert weird.kind == FailureKind.UNKNOWN and weird.confidence == 1.0


def test_routing_fails_closed() -> None:
    log = _log("FAILED tests/test_a.py")
    regression = Triage.route(classify_by_rules(log.excerpt), log, "o/r", 5)  # type: ignore[arg-type]
    assert regression.name == "reenter" and regression.task is not None
    assert regression.task.source == TaskSource.CI and regression.task.issue.labels == [
        "ci",
        "test_regression",
    ]
    flake = classify_by_rules("ECONNRESET")
    assert Triage.route(flake, log, "o/r", 5).name == "retry"  # type: ignore[arg-type]
    assert Triage.route(flake, log, "o/r", 5, retries_so_far=1).name == "escalate"  # type: ignore[arg-type]
    env = classify_by_rules("ModuleNotFoundError")
    assert Triage.route(env, log, "o/r", None).name == "escalate"  # type: ignore[arg-type]


def _api(routes: dict[str, Any], calls: list[tuple[str, str]]) -> GitHubApi:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        for suffix, payload in routes.items():
            if request.url.path.endswith(suffix):
                if isinstance(payload, str):
                    return httpx.Response(200, text=payload)
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    return GitHubApi(
        "t",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test"),
    )


def test_actions_client_extracts_first_failure() -> None:
    calls: list[tuple[str, str]] = []
    api = _api(
        {
            "/actions/runs": {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "head_sha": "h",
                    },
                    {
                        "id": 2,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_sha": "h",
                        "html_url": "u",
                    },
                ]
            },
            "/runs/2/jobs": {
                "jobs": [
                    {"id": 20, "name": "lint", "conclusion": "success", "steps": []},
                    {
                        "id": 21,
                        "name": "verify",
                        "conclusion": "failure",
                        "steps": [{"name": "pytest", "conclusion": "failure"}],
                    },
                ]
            },
            "/jobs/21/logs": (
                "collecting\nFAILED tests/test_x.py::test_a\n"
                "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
            ),
        },
        calls,
    )
    log = ActionsClient(api).first_failure("o/r", "h")
    assert log is not None and log.job.failed_step == "pytest" and log.scrubbed == 1
    assert "ghp_" not in log.excerpt and log.total_lines == 3


def test_ci_watcher_labels_and_routes() -> None:
    store = MemoryRunStore()
    run = AgentRun(issue=Issue(repository="o/r", number=3, title="t"), pr_number=9)
    calls: list[tuple[str, str]] = []
    api = _api(
        {
            "/pulls/9": {"head": {"sha": "h"}},
            "/actions/runs": {
                "workflow_runs": [
                    {
                        "id": 2,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_sha": "h",
                    }
                ]
            },
            "/runs/2/jobs": {
                "jobs": [
                    {
                        "id": 21,
                        "name": "verify",
                        "conclusion": "failure",
                        "steps": [{"name": "pytest", "conclusion": "failure"}],
                    }
                ]
            },
            "/jobs/21/logs": "FAILED tests/test_x.py::test_a - AssertionError",
        },
        calls,
    )
    outcome = CiWatcher(api, store).check(run)
    assert (
        outcome.status == "red" and outcome.action is not None and outcome.action.name == "reenter"
    )
    assert outcome.task is not None and outcome.task.source == TaskSource.CI
    labels = store.reviews(run.id)
    assert (
        labels[0].reviewer == "github-actions"
        and labels[0].decision == ReviewDecision.CHANGES_REQUESTED
    )

    flaky = _api(
        {
            "/pulls/9": {"head": {"sha": "h"}},
            "/actions/runs": {
                "workflow_runs": [
                    {
                        "id": 2,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_sha": "h",
                    }
                ]
            },
            "/runs/2/jobs": {
                "jobs": [{"id": 21, "name": "verify", "conclusion": "failure", "steps": []}]
            },
            "/jobs/21/logs": "ECONNRESET",
            "/rerun-failed-jobs": {},
        },
        calls,
    )
    assert CiWatcher(flaky, store).check(run).rerun_requested

    unknown = _api(
        {
            "/pulls/9": {"head": {"sha": "h"}},
            "/actions/runs": {
                "workflow_runs": [
                    {
                        "id": 2,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_sha": "h",
                    }
                ]
            },
            "/runs/2/jobs": {
                "jobs": [{"id": 21, "name": "verify", "conclusion": "failure", "steps": []}]
            },
            "/jobs/21/logs": "???",
            "/issues/9/comments": {},
        },
        calls,
    )
    assert CiWatcher(unknown, store).check(run).comment_posted

    green = _api(
        {
            "/pulls/9": {"head": {"sha": "h"}},
            "/actions/runs": {
                "workflow_runs": [
                    {"id": 2, "name": "CI", "status": "completed", "conclusion": "success"}
                ]
            },
        },
        calls,
    )
    assert CiWatcher(green, store).check(run).status == "green"
    pending = _api(
        {
            "/pulls/9": {"head": {"sha": "h"}},
            "/actions/runs": {"workflow_runs": [{"id": 2, "name": "CI", "status": "in_progress"}]},
        },
        calls,
    )
    assert CiWatcher(pending, store).check(run).status == "pending"
    none = _api({"/pulls/9": {"head": {"sha": "h"}}, "/actions/runs": {"workflow_runs": []}}, calls)
    assert CiWatcher(none, store).check(run).status == "unknown"
    no_log = _api(
        {
            "/pulls/9": {"head": {"sha": "h"}},
            "/actions/runs": {
                "workflow_runs": [
                    {"id": 2, "name": "CI", "status": "completed", "conclusion": "failure"}
                ]
            },
            "/runs/2/jobs": {"jobs": []},
        },
        calls,
    )
    assert CiWatcher(no_log, store).check(run).action is None
    with pytest.raises(ValueError):
        CiWatcher(none, store).check(AgentRun(issue=run.issue))


def test_canary_decisions_and_denied_deploy() -> None:
    controller = CanaryController(
        PolicyEngine(), Slo(max_error_rate=0.02, max_p95_ms=300, min_requests=10)
    )
    healthy = Window(requests=100, errors=1, p95_ms=120)
    assert controller.evaluate(10, healthy).weight == 25
    assert controller.evaluate(100, healthy).action == "promote"
    assert controller.evaluate(10, Window(requests=5, errors=0, p95_ms=1)).action == "hold"
    assert controller.evaluate(10, Window(requests=100, errors=10, p95_ms=1)).action == "rollback"
    assert controller.evaluate(10, Window(requests=100, errors=0, p95_ms=900)).action == "rollback"
    assert controller.evaluate(33, healthy).action == "promote"

    report = controller.simulate("img:1", [healthy, healthy, healthy, healthy])
    assert report.final == "promoted" and [d.weight for d in report.decisions] == [25, 50, 100, 100]
    assert (
        controller.simulate("img:2", [healthy, Window(requests=100, errors=50, p95_ms=1)]).final
        == "rolled_back"
    )
    assert controller.simulate("img:3", [healthy]).final == "hold"
    with pytest.raises(AuthorityDenied):
        controller.deploy("img:1")
    with pytest.raises(NotImplementedError):
        CanaryController(PolicyEngine(Policy(allow_deploy=True))).deploy("img:1")
