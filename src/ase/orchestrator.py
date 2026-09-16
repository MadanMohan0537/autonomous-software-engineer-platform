"""Explicit, resumable workflow with approval gates."""

from __future__ import annotations

from pathlib import Path

from ase.contracts import AgentRun, Decision, Issue, RunState
from ase.planning import DeterministicPlanner, Planner
from ase.repo_intelligence import HybridRetriever, RepositoryIndexer
from ase.store import MemoryRunStore, PlatformStore


class InvalidTransition(RuntimeError):
    pass


class Orchestrator:
    def __init__(self, store: PlatformStore | None = None, planner: Planner | None = None) -> None:
        self.store: PlatformStore = store or MemoryRunStore()
        self.planner = planner or DeterministicPlanner()
        self.indexer = RepositoryIndexer()
        self.retriever = HybridRetriever()

    def create(self, issue: Issue) -> AgentRun:
        run = AgentRun(issue=issue)
        run.record("run_created")
        self.store.save(run)
        return run

    async def analyze(self, run_id: str, repository: Path) -> AgentRun:
        run = self._require(run_id)
        self._expect(run, {RunState.INGEST_ISSUE, RunState.RETRIEVE_CONTEXT})
        run.state = RunState.RETRIEVE_CONTEXT
        index = self.indexer.index(repository)
        run.context = self.retriever.retrieve(run.issue, index)
        run.record("context_retrieved", files=[item.path for item in run.context])
        run.state = RunState.PROPOSE_PLAN
        run.plan = await self.planner.plan(run.issue, run.context)
        run.record("plan_proposed", summary=run.plan.summary)
        run.state = RunState.AWAIT_PLAN_APPROVAL
        self.store.save(run)
        return run

    def approve_plan(self, run_id: str, approved: bool, reason: str = "") -> AgentRun:
        run = self._require(run_id)
        self._expect(run, {RunState.AWAIT_PLAN_APPROVAL})
        if run.plan is None:
            raise InvalidTransition("run has no plan")
        run.plan.approval = Decision.APPROVED if approved else Decision.REJECTED
        run.record("plan_reviewed", approved=approved, reason=reason)
        run.state = RunState.CREATE_WORKSPACE if approved else RunState.FAILED
        self.store.save(run)
        return run

    def record_evaluation(self, run_id: str, report: object) -> AgentRun:
        from ase.contracts import EvaluationReport

        if not isinstance(report, EvaluationReport):
            raise TypeError("report must be an EvaluationReport")
        run = self._require(run_id)
        self._expect(run, {RunState.VERIFY_PATCH, RunState.CREATE_WORKSPACE})
        run.evaluation = report
        run.record("patch_evaluated", passed=report.passed)
        run.state = RunState.AWAIT_PR_APPROVAL if report.passed else RunState.FAILED
        self.store.save(run)
        return run

    def approve_pr(self, run_id: str, approved: bool, reason: str = "") -> AgentRun:
        """The second human gate: a verified patch still needs a named approval to become a PR."""
        run = self._require(run_id)
        self._expect(run, {RunState.AWAIT_PR_APPROVAL})
        run.record("pr_reviewed", approved=approved, reason=reason)
        run.state = RunState.OPEN_DRAFT_PR if approved else RunState.FAILED
        if not approved:
            run.outcome = "rejected"
        self.store.save(run)
        return run

    def _require(self, run_id: str) -> AgentRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    @staticmethod
    def _expect(run: AgentRun, states: set[RunState]) -> None:
        if run.state not in states:
            expected = ", ".join(sorted(item.value for item in states))
            raise InvalidTransition(
                f"state {run.state.value} cannot perform action; expected {expected}"
            )
