"""Run pytest inside a sandbox and turn the result into a `TestReport`.

The report is the platform's ground truth. It is built from pytest's JUnit XML (the
`xunit1` family carries file paths), never from parsing free-form console output.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path

from ase.contracts import CommandResult, TestReport
from ase.sandbox import Sandbox

TestStatus = str  # "passed" | "failed" | "error" | "skipped"

REPORT_DIR = ".ase-run"
REPORT_NAME = "junit.xml"


def pytest_argv(
    python: str, test_ids: Iterable[str] = (), extra: Iterable[str] = (), report: str | None = None
) -> list[str]:
    argv = [
        python,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit1",
        "--junit-xml",
        report or f"{REPORT_DIR}/{REPORT_NAME}",
        *extra,
        *test_ids,
    ]
    return argv


def node_id(file: str, classname: str, name: str) -> str:
    """Rebuild a pytest node id from xunit1 attributes."""
    module = file[:-3].replace("/", ".") if file.endswith(".py") else file.replace("/", ".")
    if classname.startswith(module + "."):
        nested = classname[len(module) + 1 :].replace(".", "::")
        return f"{file}::{nested}::{name}"
    return f"{file}::{name}"


def parse_junit(xml_text: str) -> dict[str, TestStatus]:
    statuses: dict[str, TestStatus] = {}
    root = ET.fromstring(xml_text)
    for case in root.iter("testcase"):
        file = case.get("file") or case.get("classname", "").replace(".", "/") + ".py"
        identifier = node_id(file, case.get("classname", ""), case.get("name", ""))
        if case.find("failure") is not None:
            statuses[identifier] = "failed"
        elif case.find("error") is not None:
            statuses[identifier] = "error"
        elif case.find("skipped") is not None:
            statuses[identifier] = "skipped"
        else:
            statuses[identifier] = "passed"
    return statuses


def build_report(
    run_id: str,
    step_index: int,
    statuses: Mapping[str, TestStatus],
    fail_to_pass: Iterable[str] = (),
    pass_to_pass: Iterable[str] = (),
    stdout_tail: str = "",
) -> TestReport:
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    f2p = {name: statuses.get(name) == "passed" for name in fail_to_pass}
    p2p = {name: statuses.get(name) == "passed" for name in pass_to_pass}
    return TestReport(
        run_id=run_id,
        step_index=step_index,
        fail_to_pass=f2p,
        pass_to_pass=p2p,
        total=len(statuses),
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["error"],
        skipped=counts["skipped"],
        stdout_tail=stdout_tail,
    )


def tail(text: str, lines: int = 100) -> str:
    return "\n".join(text.splitlines()[-lines:])


class PytestRunner:
    """Runs pytest through any sandbox and reads back the JUnit report from the worktree."""

    def __init__(self, sandbox: Sandbox, python: str | None = None) -> None:
        self.sandbox = sandbox
        self.python = python or sys.executable

    def run(
        self,
        test_ids: Iterable[str] = (),
        extra: Iterable[str] = (),
        timeout: int | None = None,
    ) -> tuple[CommandResult, dict[str, TestStatus]]:
        report_dir = self.sandbox.root / REPORT_DIR
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / REPORT_NAME
        if report_path.exists():
            report_path.unlink()
        result = self.sandbox.run(pytest_argv(self.python, test_ids, extra), timeout=timeout)
        statuses: dict[str, TestStatus] = {}
        if report_path.exists():
            statuses = parse_junit(report_path.read_text(encoding="utf-8"))
            report_path.unlink()
        return result, statuses

    def report(
        self,
        run_id: str,
        step_index: int,
        fail_to_pass: Iterable[str] = (),
        pass_to_pass: Iterable[str] = (),
        timeout: int | None = None,
    ) -> TestReport:
        selected = [*fail_to_pass, *pass_to_pass]
        result, statuses = self.run(selected, timeout=timeout)
        report = build_report(
            run_id,
            step_index,
            statuses,
            fail_to_pass,
            pass_to_pass,
            stdout_tail=tail(result.stdout + "\n" + result.stderr),
        )
        if not statuses and result.exit_code not in (0, 5):
            # pytest could not even collect: treat as an error so nothing reads as green.
            report.errors += 1
        return report


def report_path(root: Path) -> Path:
    return root / REPORT_DIR / REPORT_NAME
