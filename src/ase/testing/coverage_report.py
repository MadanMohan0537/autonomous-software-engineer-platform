"""Line coverage through coverage.py, parsed from its JSON report.

Coverage answers "which lines never ran"; it says nothing about whether the tests that
ran them would notice a bug. That second question is what `mutation.py` answers.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field

from ase.sandbox import Sandbox
from ase.testrun import REPORT_DIR

COVERAGE_JSON = "coverage.json"


class FileCoverage(BaseModel):
    path: str
    percent: float
    executed_lines: list[int] = Field(default_factory=list)
    missing_lines: list[int] = Field(default_factory=list)

    @property
    def missing_ranges(self) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        for line in self.missing_lines:
            if ranges and line == ranges[-1][1] + 1:
                ranges[-1] = (ranges[-1][0], line)
            else:
                ranges.append((line, line))
        return ranges


class CoverageReport(BaseModel):
    percent: float
    files: dict[str, FileCoverage] = Field(default_factory=dict)

    def weakest(self, limit: int = 5) -> list[FileCoverage]:
        return sorted(self.files.values(), key=lambda item: item.percent)[:limit]


def parse_coverage_json(text: str) -> CoverageReport:
    data = json.loads(text)
    files: dict[str, FileCoverage] = {}
    for raw_path, entry in (data.get("files") or {}).items():
        summary = entry.get("summary") or {}
        path = str(raw_path).replace("\\", "/")
        files[path] = FileCoverage(
            path=path,
            percent=float(summary.get("percent_covered", 0.0)),
            executed_lines=sorted(int(line) for line in entry.get("executed_lines", [])),
            missing_lines=sorted(int(line) for line in entry.get("missing_lines", [])),
        )
    totals = data.get("totals") or {}
    return CoverageReport(percent=float(totals.get("percent_covered", 0.0)), files=files)


def coverage_argv(python: str, sources: Iterable[str], test_ids: Iterable[str]) -> list[list[str]]:
    source = ",".join(sources) or "."
    return [
        [
            python,
            "-m",
            "coverage",
            "run",
            f"--source={source}",
            "--data-file",
            f"{REPORT_DIR}/.coverage",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *test_ids,
        ],
        [
            python,
            "-m",
            "coverage",
            "json",
            "--data-file",
            f"{REPORT_DIR}/.coverage",
            "-o",
            f"{REPORT_DIR}/{COVERAGE_JSON}",
        ],
    ]


class CoverageRunner:
    def __init__(self, sandbox: Sandbox, python: str | None = None) -> None:
        self.sandbox = sandbox
        self.python = python or sys.executable

    def run(self, sources: Iterable[str], test_ids: Iterable[str] = ()) -> CoverageReport | None:
        """Returns None when coverage.py is not installed in the sandbox environment."""
        report_dir = self.sandbox.root / REPORT_DIR
        report_dir.mkdir(parents=True, exist_ok=True)
        target = report_dir / COVERAGE_JSON
        if target.exists():
            target.unlink()
        for argv in coverage_argv(self.python, sources, test_ids):
            result = self.sandbox.run(argv)
            if "No module named coverage" in result.stderr:
                return None
        if not target.exists():
            return None
        return parse_coverage_json(target.read_text(encoding="utf-8"))


def describe_uncovered(
    report: CoverageReport, root: Path, limit_files: int = 5, context: int = 3
) -> str:
    """Render uncovered ranges with surrounding source for the test generator's prompt."""
    parts: list[str] = []
    for item in report.weakest(limit_files):
        path = root / item.path
        if not path.is_file() or not item.missing_lines:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        parts.append(f"## {item.path} ({item.percent:.0f}% covered)")
        for start, end in item.missing_ranges[:8]:
            low, high = max(1, start - context), min(len(lines), end + context)
            snippet = "\n".join(
                f"{number:>4}{'*' if start <= number <= end else ' '} {lines[number - 1]}"
                for number in range(low, high + 1)
            )
            parts.append(f"uncovered lines {start}-{end}:\n{snippet}")
    return "\n\n".join(parts)
