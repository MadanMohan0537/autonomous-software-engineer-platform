"""Publish a workspace's changes as a branch and a draft pull request.

The agent never holds the GitHub token and never runs `git push`. This adapter reads the
changed files from the worktree and writes them through the Git Data API: blobs, a tree
on top of the base commit, a commit, a ref. Deletions are tree entries with a null SHA.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ase.github import DraftPullRequest, GitHubApi


@dataclass(frozen=True)
class Published:
    branch: str
    commit_sha: str
    pr_url: str
    pr_number: int


class Delivery(Protocol):
    def publish(
        self,
        remote: str,
        base_sha: str,
        branch: str,
        workspace: Path,
        changed_files: list[str],
        commit_message: str,
        title: str,
        body: str,
    ) -> Published: ...


def branch_name(run_id: str, issue_number: int) -> str:
    return f"ase/issue-{issue_number}-{run_id.replace('run_', '')}"


def sanitize_title(title: str, issue_number: int) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()[:72]
    return f"[draft] #{issue_number}: {cleaned}"


class GitHubDelivery:
    def __init__(self, api: GitHubApi) -> None:
        self.api = api

    def publish(
        self,
        remote: str,
        base_sha: str,
        branch: str,
        workspace: Path,
        changed_files: list[str],
        commit_message: str,
        title: str,
        body: str,
    ) -> Published:
        entries: list[dict[str, Any]] = []
        for relative in changed_files:
            path = workspace / relative
            if path.is_file():
                blob = self.api.create_blob(remote, path.read_text(encoding="utf-8"))
                entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob})
            else:
                entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
        base_tree = self.api.get_commit_tree(remote, base_sha)
        tree = self.api.create_tree(remote, base_tree, entries)
        commit = self.api.create_commit(remote, commit_message, tree, [base_sha])
        self.api.create_ref(remote, f"refs/heads/{branch}", commit)
        default_branch = str(self.api.get_repository(remote).get("default_branch", "main"))
        pull = self.api.create_draft_pull_request(
            DraftPullRequest(
                repository=remote, title=title, body=body, head=branch, base=default_branch
            )
        )
        return Published(
            branch=branch,
            commit_sha=commit,
            pr_url=str(pull.get("html_url", "")),
            pr_number=int(pull.get("number", 0)),
        )


class DryRunDelivery:
    """Writes the patch to disk instead of GitHub. Used without a token and in tests."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.published: list[Published] = []

    def publish(
        self,
        remote: str,
        base_sha: str,
        branch: str,
        workspace: Path,
        changed_files: list[str],
        commit_message: str,
        title: str,
        body: str,
    ) -> Published:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{branch.replace('/', '_')}.md"
        target.write_text(
            f"# {title}\n\nbranch: {branch}\nbase: {base_sha}\n"
            f"files: {', '.join(changed_files)}\n\n{body}\n",
            encoding="utf-8",
        )
        published = Published(
            branch=branch,
            commit_sha=base_sha,
            pr_url=f"file://{target.resolve()}",
            pr_number=len(self.published) + 1,
        )
        self.published.append(published)
        return published
