"""Minimal GitHub API boundary with dry-run-first pull-request behavior."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


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
                "X-GitHub-Api-Version": "2022-11-28",
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
