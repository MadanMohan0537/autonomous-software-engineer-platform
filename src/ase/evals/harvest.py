"""Harvest replay tasks from a repository's own history.

Every commit that changed both production code and tests is a candidate: the commit
message is the issue, the parent commit is the starting point, and the tests the commit
added or changed are the acceptance check. A test that fails on the parent (with the
new tests applied) and passes on the commit is a genuine fail-to-pass test, measured, not
assumed. Merged pull requests can be harvested through the GitHub API as well, with the
fail-to-pass tests left to be measured the same way.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ase.contracts import looks_like_test
from ase.evals.suites import EvalTask, Suite
from ase.github import GitHubApi
from ase.sandbox import Sandbox
from ase.testrun import PytestRunner
from ase.workspace import WorkspaceManager

SandboxFactory = Callable[[Path], Sandbox]


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, capture_output=True, text=True, check=True, timeout=120
    ).stdout


def candidate_commits(repository: Path, limit: int = 20) -> list[tuple[str, str, str, list[str]]]:
    """(sha, subject, body, files) for commits touching both tests and production code."""
    output = _git(
        repository,
        "log",
        f"--max-count={limit * 4}",
        "--no-merges",
        "--format=%x1e%H%x1f%s%x1f%b",
        "--name-only",
    )
    candidates: list[tuple[str, str, str, list[str]]] = []
    for block in output.split("\x1e"):
        if not block.strip():
            continue
        header, _, files_text = block.partition("\n")
        parts = header.split("\x1f")
        sha, subject = parts[0], parts[1] if len(parts) > 1 else ""
        body = parts[2] if len(parts) > 2 else ""
        files = [line.strip() for line in files_text.splitlines() if line.strip().endswith(".py")]
        tests = [item for item in files if looks_like_test(item)]
        sources = [item for item in files if not looks_like_test(item)]
        if tests and sources and subject:
            candidates.append((sha, subject, body.strip(), files))
        if len(candidates) >= limit:
            break
    return candidates


class GitHarvester:
    def __init__(
        self,
        workspaces: WorkspaceManager,
        sandbox_factory: SandboxFactory,
        python: str | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.sandbox_factory = sandbox_factory
        self.python = python

    def harvest(self, repository: Path, limit: int = 5, name: str = "own-repo-replay") -> Suite:
        suite = Suite(name=name, description=f"replay of {limit} commits from {repository.name}")
        for sha, subject, body, files in candidate_commits(repository, limit * 2):
            task = self._measure(repository, sha, subject, body, files)
            if task is not None:
                suite.tasks.append(task)
            if len(suite.tasks) >= limit:
                break
        return suite

    def _measure(
        self, repository: Path, sha: str, subject: str, body: str, files: list[str]
    ) -> EvalTask | None:
        try:
            parent = _git(repository, "rev-parse", f"{sha}^").strip()
        except subprocess.CalledProcessError:
            return None  # a root commit has nothing to replay against
        test_files = [item for item in files if looks_like_test(item)]
        test_patch: dict[str, str] = {}
        for path in test_files:
            try:
                test_patch[path] = _git(repository, "show", f"{sha}:{path}")
            except subprocess.CalledProcessError:
                continue  # deleted test file
        if not test_patch:
            return None

        after = self.workspaces.create(repository, sha)
        before = self.workspaces.create(repository, parent)
        try:
            statuses_after = self._run(after.path, list(test_patch))
            for path, content in test_patch.items():
                target = before.path / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            statuses_before = self._run(before.path, list(test_patch))
        finally:
            self.workspaces.remove(after)
            self.workspaces.remove(before)

        fail_to_pass = sorted(
            name
            for name, status in statuses_after.items()
            if status == "passed" and statuses_before.get(name) in {"failed", "error"}
        )
        pass_to_pass = sorted(
            name
            for name, status in statuses_after.items()
            if status == "passed" and statuses_before.get(name) == "passed"
        )
        if not fail_to_pass:
            return None
        return EvalTask(
            id=sha[:10],
            repository=str(repository),
            base_sha=parent,
            title=subject,
            body=body,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            test_patch=test_patch,
            expected_files=[item for item in files if not looks_like_test(item)],
            metadata={"fix_sha": sha},
        )

    def _run(self, root: Path, test_files: list[str]) -> dict[str, str]:
        _, statuses = PytestRunner(self.sandbox_factory(root), self.python).run(test_files)
        return statuses


def harvest_from_pull_requests(
    api: GitHubApi, repository: str, limit: int = 10, name: str = "merged-prs"
) -> Suite:
    """Merged PRs as tasks. Fail-to-pass tests are measured later with `GitHarvester`."""
    suite = Suite(name=name, description=f"merged pull requests from {repository}")
    for pull in api.list_pull_requests(repository, state="closed", per_page=limit * 3):
        if not pull.get("merged_at"):
            continue
        files = [
            str(item.get("filename"))
            for item in api.list_pull_request_files(repository, int(pull["number"]))
        ]
        tests = [item for item in files if looks_like_test(item)]
        sources = [item for item in files if item.endswith(".py") and not looks_like_test(item)]
        if not tests or not sources:
            continue
        suite.tasks.append(
            EvalTask(
                id=f"pr-{pull['number']}",
                repository=repository,
                base_sha=str(pull.get("base", {}).get("sha") or "") or None,
                title=str(pull.get("title", "")),
                body=str(pull.get("body") or ""),
                expected_files=sources,
                metadata={"merge_commit_sha": pull.get("merge_commit_sha"), "test_files": tests},
            )
        )
        if len(suite.tasks) >= limit:
            break
    return suite
