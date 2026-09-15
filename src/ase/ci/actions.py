"""GitHub Actions: find the failing run for a commit and extract a small, scrubbed log.

Logs can be enormous and can contain secrets. Nothing leaves this module unless it has
been trimmed to the failing step's interesting window and had known credential shapes
masked.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ase.github import GitHubApi

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]{16,}=*"),
    re.compile(r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)[^\s'\"]{6,}"),
]
ERROR_MARKERS = (
    "Traceback (most recent call last)",
    "##[error]",
    "Error:",
    "FAILED ",
    "AssertionError",
    "error[",
    "ERROR:",
)


class WorkflowRun(BaseModel):
    id: int
    name: str
    status: str
    conclusion: str | None = None
    html_url: str = ""
    head_sha: str = ""


class Job(BaseModel):
    id: int
    name: str
    conclusion: str | None = None
    failed_step: str | None = None


class FailureLog(BaseModel):
    run: WorkflowRun
    job: Job
    excerpt: str
    total_lines: int = 0
    truncated: bool = False
    scrubbed: int = Field(default=0, description="number of secret-shaped strings masked")


def scrub_secrets(text: str) -> tuple[str, int]:
    count = 0
    for pattern in SECRET_PATTERNS:

        def mask(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            prefix = match.group(1) if match.groups() else ""
            return f"{prefix}***"

        text = pattern.sub(mask, text)
    return text, count


def trim_log(text: str, window: int = 40, tail_lines: int = 200) -> tuple[str, bool]:
    """Keep the first error window and the tail; drop the rest."""
    lines = text.splitlines()
    if len(lines) <= tail_lines:
        return "\n".join(lines), False
    first_error = next(
        (index for index, line in enumerate(lines) if any(m in line for m in ERROR_MARKERS)),
        None,
    )
    parts: list[str] = []
    tail_start = len(lines) - tail_lines
    if first_error is not None and first_error < tail_start:
        parts.extend(lines[first_error : first_error + window])
        parts.append(f"... [{tail_start - first_error - window} lines omitted] ...")
    parts.extend(lines[tail_start:])
    return "\n".join(parts), True


class ActionsClient:
    def __init__(self, api: GitHubApi) -> None:
        self.api = api

    def runs_for(self, repository: str, head_sha: str) -> list[WorkflowRun]:
        return [
            WorkflowRun(
                id=int(item["id"]),
                name=str(item.get("name", "")),
                status=str(item.get("status", "")),
                conclusion=item.get("conclusion"),
                html_url=str(item.get("html_url", "")),
                head_sha=str(item.get("head_sha", "")),
            )
            for item in self.api.list_workflow_runs(repository, head_sha)
        ]

    def failed_jobs(self, repository: str, run_id: int) -> list[Job]:
        jobs: list[Job] = []
        for item in self.api.list_jobs(repository, run_id):
            if item.get("conclusion") != "failure":
                continue
            steps: list[dict[str, Any]] = list(item.get("steps") or [])
            failed_step = next(
                (str(step.get("name")) for step in steps if step.get("conclusion") == "failure"),
                None,
            )
            jobs.append(
                Job(
                    id=int(item["id"]),
                    name=str(item.get("name", "")),
                    conclusion=item.get("conclusion"),
                    failed_step=failed_step,
                )
            )
        return jobs

    def failure_log(self, repository: str, run: WorkflowRun, job: Job) -> FailureLog:
        raw = self.api.job_log(repository, job.id)
        trimmed, truncated = trim_log(raw)
        scrubbed, count = scrub_secrets(trimmed)
        return FailureLog(
            run=run,
            job=job,
            excerpt=scrubbed,
            total_lines=len(raw.splitlines()),
            truncated=truncated,
            scrubbed=count,
        )

    def first_failure(self, repository: str, head_sha: str) -> FailureLog | None:
        for run in self.runs_for(repository, head_sha):
            if run.conclusion != "failure":
                continue
            for job in self.failed_jobs(repository, run.id):
                return self.failure_log(repository, run, job)
        return None
