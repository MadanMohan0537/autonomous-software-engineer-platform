"""Mirror human decisions on the agent's draft pull requests into the trace store.

GitHub is the review UI. This module only reads: PR reviews (approved, changes
requested, commented) and the PR's own state (merged, closed) become `Review` records
attributed to the reviewer who made them. CI results are recorded separately by the CI
watcher and attributed to CI, so a human label is never confused with a machine one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ase.contracts import AgentRun, Review, ReviewDecision
from ase.github import GitHubApi
from ase.store import PlatformStore

_STATE_TO_DECISION = {
    "APPROVED": ReviewDecision.APPROVED,
    "CHANGES_REQUESTED": ReviewDecision.CHANGES_REQUESTED,
    "COMMENTED": ReviewDecision.COMMENTED,
}


def _parse_time(value: object) -> datetime:
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(UTC)


class ReviewSync:
    def __init__(self, api: GitHubApi, store: PlatformStore) -> None:
        self.api = api
        self.store = store

    def sync(self, run: AgentRun) -> list[Review]:
        if run.pr_number is None:
            return []
        repository = run.issue.repository
        existing = {
            (item.pr_number, item.reviewer, item.decision.value, item.at.isoformat())
            for item in self.store.reviews(run.id)
        }
        added: list[Review] = []
        for raw in self.api.list_pull_request_reviews(repository, run.pr_number):
            decision = _STATE_TO_DECISION.get(str(raw.get("state", "")).upper())
            if decision is None:
                continue
            review = Review(
                run_id=run.id,
                pr_number=run.pr_number,
                decision=decision,
                reviewer=str((raw.get("user") or {}).get("login", "")),
                comments=[str(raw.get("body") or "")] if raw.get("body") else [],
                at=_parse_time(raw.get("submitted_at")),
            )
            key = (review.pr_number, review.reviewer, review.decision.value, review.at.isoformat())
            if key in existing:
                continue
            self.store.add_review(review)
            existing.add(key)
            added.append(review)

        pull = self.api.get_pull_request(repository, run.pr_number)
        terminal: ReviewDecision | None = None
        if pull.get("merged_at"):
            terminal = ReviewDecision.MERGED
        elif str(pull.get("state", "")) == "closed":
            terminal = ReviewDecision.CLOSED
        if terminal is not None:
            at = _parse_time(pull.get("merged_at") or pull.get("closed_at"))
            reviewer = str((pull.get("merged_by") or {}).get("login", "") or "")
            key = (run.pr_number, reviewer, terminal.value, at.isoformat())
            if key not in existing:
                review = Review(
                    run_id=run.id,
                    pr_number=run.pr_number,
                    decision=terminal,
                    reviewer=reviewer,
                    at=at,
                )
                self.store.add_review(review)
                added.append(review)
        return added

    def sync_all(self, runs: list[AgentRun]) -> int:
        return sum(len(self.sync(run)) for run in runs if run.pr_number is not None)
