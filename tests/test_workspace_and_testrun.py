import subprocess
from pathlib import Path
from typing import Any

import pytest

from ase.sandbox import LocalSandbox
from ase.testrun import PytestRunner, build_report, node_id, parse_junit, pytest_argv, tail
from ase.workspace import WorkspaceError, WorkspaceManager

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.test_calc" file="tests/test_calc.py" line="4" name="test_a"/>
<testcase classname="tests.test_calc" file="tests/test_calc.py" line="8" name="test_b">
  <failure message="assert 1 == 2">boom</failure></testcase>
<testcase classname="tests.test_calc.TestGroup" file="tests/test_calc.py" line="12" name="test_c">
  <error message="fixture">err</error></testcase>
<testcase classname="tests.test_calc" file="tests/test_calc.py" line="16" name="test_d[1]">
  <skipped message="skip"/></testcase>
</testsuite></testsuites>
"""


def test_node_id_reconstruction() -> None:
    assert (
        node_id("tests/test_calc.py", "tests.test_calc", "test_a") == "tests/test_calc.py::test_a"
    )
    assert (
        node_id("tests/test_calc.py", "tests.test_calc.TestGroup", "test_c")
        == "tests/test_calc.py::TestGroup::test_c"
    )
    assert node_id("x.py", "other", "t") == "x.py::t"


def test_parse_junit_and_build_report() -> None:
    statuses = parse_junit(JUNIT)
    assert statuses["tests/test_calc.py::test_a"] == "passed"
    assert statuses["tests/test_calc.py::test_b"] == "failed"
    assert statuses["tests/test_calc.py::TestGroup::test_c"] == "error"
    assert statuses["tests/test_calc.py::test_d[1]"] == "skipped"
    report = build_report(
        "run", 1, statuses, ["tests/test_calc.py::test_b"], ["tests/test_calc.py::test_a"], "out"
    )
    assert report.total == 4 and report.failed == 1 and report.errors == 1 and report.skipped == 1
    assert not report.green and report.fail_to_pass == {"tests/test_calc.py::test_b": False}
    assert tail("a\nb\nc", 2) == "b\nc"
    assert pytest_argv("python", ["t::x"], ["-x"])[-2:] == ["-x", "t::x"]


def test_worktree_lifecycle_and_live_pytest(fixture_repo: Path, tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws")
    workspace = manager.create(fixture_repo)
    assert workspace.path.is_dir() and len(workspace.base_sha) == 40
    assert manager.diff(workspace) == ""

    runner = PytestRunner(LocalSandbox(workspace.path))
    report = runner.report(
        "run",
        1,
        fail_to_pass=["tests/test_calc.py::test_refund_zero_adjustment_is_allowed"],
        pass_to_pass=["tests/test_calc.py::test_discount_basic"],
    )
    failing = "tests/test_calc.py::test_refund_zero_adjustment_is_allowed"
    assert report.fail_to_pass == {failing: False}
    assert report.pass_to_pass == {"tests/test_calc.py::test_discount_basic": True}
    assert not report.green and report.total == 2

    source = workspace.path / "pricing" / "calc.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace("adjustment <= 0", "adjustment < 0"),
        encoding="utf-8",
    )
    (workspace.path / "NEW.md").write_text("new\n", encoding="utf-8")
    diff = manager.diff(workspace)
    assert "adjustment < 0" in diff and "NEW.md" in diff
    assert manager.changed_files(workspace) == ["NEW.md", "pricing/calc.py"]

    fixed = runner.report("run", 2, ["tests/test_calc.py::test_refund_zero_adjustment_is_allowed"])
    assert fixed.green

    sha = manager.commit(workspace, "fix: allow zero adjustment")
    assert len(sha) == 40 and sha != workspace.base_sha
    manager.remove(workspace)
    assert not workspace.path.exists()


def test_collection_failure_counts_as_error(fixture_repo: Path, tmp_path: Path) -> None:
    (fixture_repo / "tests" / "test_broken.py").write_text("def broken(:\n", encoding="utf-8")
    report = PytestRunner(LocalSandbox(fixture_repo)).report("run", 1, ["tests/test_broken.py"])
    assert report.errors >= 1 and not report.green


def test_workspace_errors_surface_git_failures(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: nope")

    manager = WorkspaceManager(tmp_path, runner=runner)
    with pytest.raises(WorkspaceError):
        manager.head_sha(tmp_path)
