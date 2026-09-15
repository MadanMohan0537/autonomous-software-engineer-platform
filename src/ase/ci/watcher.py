"""Watch the agent's pull requests: red builds become labels, retries, tasks or escalations.

This module never merges and never deploys. It pushes nothing; a re-entered task goes
back through the full governed workflow, gates included.
"""

from __future__ import annotations

from pydantic import BaseModel

from ase.ci.actions import ActionsClient, FailureLog
from ase.ci.triage import Action, Classification, Triage
from ase.contracts import AgentRun, Review, ReviewDecision, Task
from ase.github import GitHubApi
from ase.store import PlatformStore


class CiOutcome(BaseModel):
    run_id: str
    pr_number: int
    head_sha: str
    status: str  # "green" | "red" | "pending" | "unknown"
    classification: Classification | None = None
    action: Action | None = None
    task: Task | None = None
    comment_posted: bool = False
    rerun_requested: bool = False


class CiWatcher:
    def __init__(
        self,
        api: GitHubApi,
        store: PlatformStore,
        triage: Triage | None = None,
        actions: ActionsClient | None = None,
    ) -> None:
        self.api = api
        self.store = store
        self.triage = triage or Triage()
        self.actions = actions or ActionsClient(api)

    def check(
        self, run: AgentRun, retries_so_far: int = 0, escalate_with_comment: bool = True
    ) -> CiOutcome:
        if run.pr_number is None:
            raise ValueError("run has no pull request")
        repository = run.issue.repository
        pull = self.api.get_pull_request(repository, run.pr_number)
        head_sha = str(pull.get("head", {}).get("sha", ""))
        runs = self.actions.runs_for(repository, head_sha)
        outcome = CiOutcome(
            run_id=run.id, pr_number=run.pr_number, head_sha=head_sha, status="unknown"
        )
        if not runs:
            return outcome
        if any(item.status != "completed" for item in runs):
            outcome.status = "pending"
            return outcome
        if all(item.conclusion == "success" for item in runs):
            outcome.status = "green"
            self._label(run, ReviewDecision.COMMENTED, "ci: success")
            return outcome

        outcome.status = "red"
        log: FailureLog | None = self.actions.first_failure(repository, head_sha)
        if log is None:
            self._label(run, ReviewDecision.CHANGES_REQUESTED, "ci: failure without a job log")
            return outcome
        classification = self.triage.classify(log)
        action = Triage.route(classification, log, repository, run.pr_number, retries_so_far)
        outcome.classification = classification
        outcome.action = action
        self._label(
            run,
            ReviewDecision.CHANGES_REQUESTED,
            f"ci: {classification.kind.value}: {classification.reason}",
        )
        if action.name == "retry":
            self.api.rerun_workflow(repository, log.run.id)
            outcome.rerun_requested = True
        elif action.name == "reenter":
            outcome.task = action.task
        elif escalate_with_comment:
            self.api.create_issue_comment(
                repository,
                run.pr_number,
                "CI failed and the agent could not classify the failure "
                f"({classification.kind.value}: {classification.reason}). "
                f"A human should look at {log.run.html_url}.",
            )
            outcome.comment_posted = True
        return outcome

    def _label(self, run: AgentRun, decision: ReviewDecision, comment: str) -> None:
        """CI results are automatic labels for the feedback loop, attributed to CI, not a human."""
        self.store.add_review(
            Review(
                run_id=run.id,
                pr_number=run.pr_number or 0,
                decision=decision,
                reviewer="github-actions",
                comments=[comment],
            )
        )
