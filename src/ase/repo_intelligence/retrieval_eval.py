"""Measure retrieval before the agent exists: recall@k against files a real fix touched.

Cases come from wherever you have ground truth: merged pull requests, or simply the git
log (commit message as the query, touched files as the answer). The number this produces
is the project's first evaluation, and it exists before a single model call is made.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ase.repo_intelligence.index import KnowledgeIndex


class RetrievalCase(BaseModel):
    name: str
    query: str
    expected_files: list[str]


class RetrievalOutcome(BaseModel):
    name: str
    retrieved: list[str]
    expected: list[str]
    hit: bool


class RetrievalReport(BaseModel):
    k: int
    cases: list[RetrievalOutcome] = Field(default_factory=list)

    @property
    def recall_at_k(self) -> float:
        if not self.cases:
            return 0.0
        return round(sum(1 for case in self.cases if case.hit) / len(self.cases), 4)


def retrieved_files(index: KnowledgeIndex, query: str, k: int) -> list[str]:
    """Distinct files in rank order until k files are collected."""
    files: list[str] = []
    for hit in index.search(query, k=k * 4):
        if hit.chunk.path not in files:
            files.append(hit.chunk.path)
        if len(files) >= k:
            break
    return files


def evaluate_retrieval(
    index: KnowledgeIndex, cases: list[RetrievalCase], k: int = 10
) -> RetrievalReport:
    report = RetrievalReport(k=k)
    for case in cases:
        files = retrieved_files(index, case.query, k)
        hit = any(expected in files for expected in case.expected_files)
        report.cases.append(
            RetrievalOutcome(name=case.name, retrieved=files, expected=case.expected_files, hit=hit)
        )
    return report


def cases_from_git_log(
    repository: Path, limit: int = 10, source_suffixes: tuple[str, ...] = (".py",)
) -> list[RetrievalCase]:
    """Build cases from recent commits: message as query, touched source files as truth."""
    output = subprocess.run(
        [
            "git",
            "log",
            f"--max-count={limit * 3}",
            "--no-merges",
            "--format=%x1e%H%x1f%s%x1f%b",
            "--name-only",
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    cases: list[RetrievalCase] = []
    for block in output.split("\x1e"):
        if not block.strip():
            continue
        header, _, files_text = block.partition("\n")
        parts = header.split("\x1f")
        sha = parts[0]
        subject = parts[1] if len(parts) > 1 else ""
        body = parts[2] if len(parts) > 2 else ""
        files = [
            line.strip()
            for line in files_text.splitlines()
            if line.strip() and line.strip().endswith(source_suffixes)
        ]
        files = [
            item
            for item in files
            if "test" not in Path(item).parts and not Path(item).name.startswith("test_")
        ]
        if not files or not subject:
            continue
        cases.append(
            RetrievalCase(name=sha[:10], query=f"{subject}\n{body}".strip(), expected_files=files)
        )
        if len(cases) >= limit:
            break
    return cases
