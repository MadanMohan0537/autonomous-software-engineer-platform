import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ase import cli
from ase.agent.service import AgentService
from ase.cli import main, parser
from ase.config import Settings
from ase.contracts import AgentRun, Issue, Review, ReviewDecision
from ase.feedback import HeuristicScorer, TrajectoryFeatures
from ase.feedback.dataset import LabeledTrajectory, export_dataset
from ase.llm import ScriptedClient, text_completion, tool_completion
from ase.store import MemoryRunStore
from tests.test_agent_flow import EDIT, plan_completion
from tests.test_evals import _commit_fix


def test_parser_requires_command() -> None:
    assert parser().prog == "ase"


def test_index_command_writes_manifest(tmp_path: Path, monkeypatch: object, capsys: object) -> None:
    (tmp_path / "app.py").write_text("def run(): pass\n", encoding="utf-8")
    output = tmp_path / "index.json"
    monkeypatch.setattr(  # type: ignore[attr-defined]
        sys, "argv", ["ase", "index", str(tmp_path), "--output", str(output)]
    )
    main()
    rendered = capsys.readouterr().out  # type: ignore[attr-defined]
    assert json.loads(rendered)["files"]["app.py"]["language"] == "python"
    assert output.exists()


def test_analyze_command(fixture_repo: Path) -> None:
    lines: list[str] = []
    code = main(
        ["analyze", str(fixture_repo), "--issue", "3", "--title", "refund zero adjustment"],
        out=lines.append,
    )
    payload = json.loads("\n".join(lines))
    assert code == 0 and payload["state"] == "await_plan_approval"


def test_knowledge_commands(
    fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASE_DATA_DIR", str(tmp_path / "data"))
    lines: list[str] = []
    assert main(["knowledge", "build", str(fixture_repo)], out=lines.append) == 0
    assert json.loads("\n".join(lines))["files"] >= 3
    lines.clear()
    assert (
        main(
            ["knowledge", "search", str(fixture_repo), "refund adjustment", "-k", "2"],
            out=lines.append,
        )
        == 0
    )
    assert "pricing/calc.py" in lines[0]
    _commit_fix(fixture_repo)
    lines.clear()
    assert main(["knowledge", "eval", str(fixture_repo), "--limit", "3"], out=lines.append) == 0
    assert lines[-1].startswith("recall@10 = ")


def _scripted_service(script: list[Any]) -> Any:
    def factory(settings: Settings, repository: Path) -> AgentService:
        return AgentService(
            settings, repository, store=MemoryRunStore(), llm=ScriptedClient(script)
        )

    return factory


def test_run_command_dry_run(
    fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        cli,
        "make_service",
        _scripted_service(
            [plan_completion(), tool_completion("edit_file", EDIT), text_completion("done")]
        ),
    )
    lines: list[str] = []
    code = main(
        [
            "run",
            str(fixture_repo),
            "--issue",
            "7",
            "--title",
            "refund zero",
            "--auto-approve",
            "--no-index",
        ],
        out=lines.append,
    )
    assert code == 0 and lines[0].startswith("run run_")
    payload = json.loads(lines[-1])
    assert payload["outcome"] == "submitted" and payload["pr_url"].startswith("file://")

    monkeypatch.setattr(cli, "make_service", _scripted_service([plan_completion()]))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    lines.clear()
    code = main(
        ["run", str(fixture_repo), "--issue", "7", "--title", "refund zero"], out=lines.append
    )
    assert code == 1 and json.loads(lines[-1])["outcome"] == "rejected"


def test_eval_commands(fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASE_DATA_DIR", str(tmp_path / "data"))
    _commit_fix(fixture_repo)
    suite_path = tmp_path / "suite.json"
    lines: list[str] = []
    assert (
        main(
            ["eval", "harvest", str(fixture_repo), "--limit", "1", "--output", str(suite_path)],
            out=lines.append,
        )
        == 0
    )
    assert "wrote 1 tasks" in lines[0]
    monkeypatch.setattr(
        cli,
        "make_service",
        _scripted_service(
            [plan_completion(), tool_completion("edit_file", EDIT), text_completion("done")]
        ),
    )
    results_dir = tmp_path / "results"
    lines.clear()
    assert (
        main(
            [
                "eval",
                "run",
                str(fixture_repo),
                "--suite",
                str(suite_path),
                "--results-dir",
                str(results_dir),
                "--no-index",
            ],
            out=lines.append,
        )
        == 0
    )
    assert "| own-repo-replay | structured | 1 | 1 | 100% |" in "\n".join(lines)
    lines.clear()
    assert (
        main(["eval", "report", "--results-dir", str(results_dir), "--cases"], out=lines.append)
        == 0
    )
    assert "| yes |" in "\n".join(lines)


def test_mutate_and_gen_tests_commands(
    fixture_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASE_DATA_DIR", str(tmp_path / "data"))
    lines: list[str] = []
    assert (
        main(
            ["mutate", str(fixture_repo), "--paths", "pricing/calc.py", "--budget", "120"],
            out=lines.append,
        )
        == 0
    )
    assert lines[0].startswith("mutants:")
    good = (
        "from pricing.calc import apply_discount\nimport pytest\n\n\n"
        "def test_range():\n    with pytest.raises(ValueError):\n        apply_discount(1, 101)\n"
        "    assert apply_discount(200, 50) == 100\n"
    )
    script = [
        text_completion(
            json.dumps(
                [
                    {
                        "path": "tests/test_generated_calc.py",
                        "target": "pricing/calc.py",
                        "code": good,
                    }
                ]
            )
        )
    ]
    monkeypatch.setattr(cli, "make_service", _scripted_service(script))
    lines.clear()
    assert (
        main(["gen-tests", str(fixture_repo), "--paths", "pricing/calc.py"], out=lines.append) == 0
    )
    assert any(line.startswith("kept") for line in lines)


def test_feedback_and_canary_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASE_DATA_DIR", str(tmp_path / "data"))
    store = MemoryRunStore()
    run = AgentRun(
        issue=Issue(repository="o/r", number=1, title="t"), pr_number=1, outcome="submitted"
    )
    store.save(run)
    store.add_review(
        Review(run_id=run.id, pr_number=1, decision=ReviewDecision.APPROVED, reviewer="m")
    )

    def factory(settings: Settings, repository: Path) -> AgentService:
        return AgentService(settings, repository, store=store, llm=ScriptedClient())

    monkeypatch.setattr(cli, "make_service", factory)
    dataset_path = tmp_path / "labels.jsonl"
    lines: list[str] = []
    assert main(["feedback", "dataset", "--output", str(dataset_path)], out=lines.append) == 0
    assert "exported 1" in lines[0]
    lines.clear()
    assert (
        main(
            [
                "feedback",
                "train",
                "--dataset",
                str(dataset_path),
                "--output",
                str(tmp_path / "m.json"),
            ],
            out=lines.append,
        )
        == 1
    )
    assert "not trained" in lines[0]
    features = TrajectoryFeatures(**dict.fromkeys(TrajectoryFeatures.model_fields, 0.0))
    rows = [
        LabeledTrajectory(
            run_id=f"r{i}",
            pr_number=i,
            label=i % 2,
            decision="approved",
            features=features.model_copy(update={"tests_green": float(i % 2)}),
        )
        for i in range(40)
    ]
    export_dataset(rows, dataset_path)
    lines.clear()
    assert (
        main(
            [
                "feedback",
                "train",
                "--dataset",
                str(dataset_path),
                "--output",
                str(tmp_path / "m.json"),
            ],
            out=lines.append,
        )
        == 0
    )
    lines.clear()
    assert (
        main(
            [
                "feedback",
                "agreement",
                "--dataset",
                str(dataset_path),
                "--model",
                str(tmp_path / "m.json"),
            ],
            out=lines.append,
        )
        == 0
    )
    assert json.loads("\n".join(lines))["agreement"] == 1.0
    lines.clear()
    assert main(["feedback", "agreement", "--dataset", str(dataset_path)], out=lines.append) == 0
    assert json.loads("\n".join(lines))["scorer"] == HeuristicScorer.name

    windows = tmp_path / "windows.json"
    windows.write_text(
        json.dumps([{"requests": 100, "errors": 0, "p95_ms": 100}] * 4), encoding="utf-8"
    )
    lines.clear()
    assert main(["canary", "simulate", str(windows)], out=lines.append) == 0
    assert json.loads("\n".join(lines))["final"] == "promoted"


def test_credential_gated_commands_report_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    lines: list[str] = []
    assert main(["ci", "check", "run_x"], out=lines.append) == 2
    assert main(["reviews", "sync"], out=lines.append) == 2
    assert all("GITHUB_TOKEN" in line for line in lines)
