"""GitHub API boundary: draft pull requests only, credentials only here.

Two clients live in this module. `GitHubClient` is the original async, dry-run-first
adapter used by the control plane. `GitHubApi` is the synchronous client the agent,
delivery, CI triage and review sync use; it covers issues, the Git Data API (so a branch
can be published without ever giving the agent a `git push`), pull requests, reviews and
Actions runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class DraftPullRequest:
    repository: str
    title: str
    body: str
    head: str
    base: str = "main"


class GitHubClient:
    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=30,
        )

    async def create_draft_pull_request(self, request: DraftPullRequest) -> str:
        response = await self.client.post(
            f"/repos/{request.repository}/pulls",
            json={
                "title": request.title,
                "body": request.body,
                "head": request.head,
                "base": request.base,
                "draft": True,
            },
        )
        response.raise_for_status()
        return str(response.json()["html_url"])

    async def close(self) -> None:
        await self.client.aclose()


class GitHubApi:
    """Synchronous GitHub REST client. Every method maps to one documented endpoint."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(base_url=base_url, timeout=30)
        self.client.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": API_VERSION,
            }
        )

    def _json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self.client.request(method, url, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    # -- repository ---------------------------------------------------------------------
    def get_repository(self, repository: str) -> dict[str, Any]:
        return dict(self._json("GET", f"/repos/{repository}"))

    # -- issues -------------------------------------------------------------------------
    def get_issue(self, repository: str, number: int) -> dict[str, Any]:
        data = self._json("GET", f"/repos/{repository}/issues/{number}")
        return dict(data)

    def create_issue(
        self, repository: str, title: str, body: str, labels: list[str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return dict(self._json("POST", f"/repos/{repository}/issues", json=payload))

    def create_issue_comment(self, repository: str, number: int, body: str) -> dict[str, Any]:
        return dict(
            self._json("POST", f"/repos/{repository}/issues/{number}/comments", json={"body": body})
        )

    # -- git data: publish a branch without giving anyone `git push` --------------------
    def get_ref_sha(self, repository: str, ref: str) -> str:
        data = self._json("GET", f"/repos/{repository}/git/ref/{ref}")
        return str(data["object"]["sha"])

    def create_ref(self, repository: str, ref: str, sha: str) -> None:
        self._json("POST", f"/repos/{repository}/git/refs", json={"ref": ref, "sha": sha})

    def update_ref(self, repository: str, ref: str, sha: str) -> None:
        self._json("PATCH", f"/repos/{repository}/git/{ref}", json={"sha": sha, "force": False})

    def create_blob(self, repository: str, content: str) -> str:
        data = self._json(
            "POST",
            f"/repos/{repository}/git/blobs",
            json={"content": content, "encoding": "utf-8"},
        )
        return str(data["sha"])

    def create_tree(self, repository: str, base_tree: str, entries: list[dict[str, Any]]) -> str:
        data = self._json(
            "POST",
            f"/repos/{repository}/git/trees",
            json={"base_tree": base_tree, "tree": entries},
        )
        return str(data["sha"])

    def get_commit_tree(self, repository: str, sha: str) -> str:
        data = self._json("GET", f"/repos/{repository}/git/commits/{sha}")
        return str(data["tree"]["sha"])

    def create_commit(self, repository: str, message: str, tree: str, parents: list[str]) -> str:
        data = self._json(
            "POST",
            f"/repos/{repository}/git/commits",
            json={"message": message, "tree": tree, "parents": parents},
        )
        return str(data["sha"])

    # -- pull requests and reviews ------------------------------------------------------
    def create_draft_pull_request(self, request: DraftPullRequest) -> dict[str, Any]:
        return dict(
            self._json(
                "POST",
                f"/repos/{request.repository}/pulls",
                json={
                    "title": request.title,
                    "body": request.body,
                    "head": request.head,
                    "base": request.base,
                    "draft": True,
                },
            )
        )

    def get_pull_request(self, repository: str, number: int) -> dict[str, Any]:
        return dict(self._json("GET", f"/repos/{repository}/pulls/{number}"))

    def list_pull_request_reviews(self, repository: str, number: int) -> list[dict[str, Any]]:
        data = self._json("GET", f"/repos/{repository}/pulls/{number}/reviews?per_page=100")
        return [dict(item) for item in data]

    def list_pull_request_comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        data = self._json("GET", f"/repos/{repository}/pulls/{number}/comments?per_page=100")
        return [dict(item) for item in data]

    def list_pull_requests(
        self, repository: str, state: str = "closed", per_page: int = 50
    ) -> list[dict[str, Any]]:
        data = self._json(
            "GET",
            f"/repos/{repository}/pulls?state={state}&per_page={per_page}&sort=updated&direction=desc",
        )
        return [dict(item) for item in data]

    def list_pull_request_files(self, repository: str, number: int) -> list[dict[str, Any]]:
        data = self._json("GET", f"/repos/{repository}/pulls/{number}/files?per_page=100")
        return [dict(item) for item in data]

    # -- actions ------------------------------------------------------------------------
    def list_workflow_runs(self, repository: str, head_sha: str) -> list[dict[str, Any]]:
        data = self._json(
            "GET", f"/repos/{repository}/actions/runs?head_sha={head_sha}&per_page=20"
        )
        return [dict(item) for item in data.get("workflow_runs", [])]

    def list_jobs(self, repository: str, run_id: int) -> list[dict[str, Any]]:
        data = self._json("GET", f"/repos/{repository}/actions/runs/{run_id}/jobs?per_page=50")
        return [dict(item) for item in data.get("jobs", [])]

    def job_log(self, repository: str, job_id: int) -> str:
        response = self.client.get(
            f"/repos/{repository}/actions/jobs/{job_id}/logs", follow_redirects=True
        )
        response.raise_for_status()
        return response.text

    def rerun_workflow(self, repository: str, run_id: int) -> None:
        self._json("POST", f"/repos/{repository}/actions/runs/{run_id}/rerun-failed-jobs")


def render_pr_body(run_id: str, summary: str, checks: list[tuple[str, bool]]) -> str:
    rows = "\n".join(f"| {name} | {'Passed' if passed else 'Failed'} |" for name, passed in checks)
    return f"""## Agent proposal

{summary}

## Verification

| Check | Result |
|---|---|
{rows}

## Provenance

Agent run: `{run_id}`

This is a draft. A human reviewer must approve and merge it.
"""
