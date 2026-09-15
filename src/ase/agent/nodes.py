"""Workflow nodes: pure functions of the agent state plus injected services.

The nodes know nothing about LangGraph. `graph.py` wires them into a durable graph with
interrupts at the approval gates; `runner.py` executes the same nodes sequentially for
tests and zero-dependency runs. Every node writes its evidence to the store before it
returns, and mirrors the workflow state onto the `AgentRun` the control plane exposes.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ase.agent import prompts
from ase.agent.delivery import Delivery, branch_name, sanitize_title
from ase.agent.pr import render_agent_pr_body
from ase.agent.state import (
    AgentState,
    config_of,
    context_of,
    patch_of,
    plan_of,
    report_of,
    task_of,
)
from ase.agent.tools import COMMAND_ONLY_TOOLS, STRUCTURED_TOOLS, AgentTools, python_syntax_error
from ase.contracts import (
    AgentRun,
    CheckResult,
    Decision,
    EvaluationReport,
    Patch,
    RunState,
    Step,
    Task,
    TestReport,
    ToolCall,
    looks_like_test,
)
from ase.llm.client import LLMClient, Message, ToolResultBlock
from ase.llm.pricing import estimate_cost
from ase.policy import PolicyEngine
from ase.repo_intelligence import (
    HybridRetriever,
    KnowledgeIndex,
    KnowledgeRetriever,
    RepositoryIndexer,
)
from ase.sandbox import Sandbox, build_sandbox
from ase.store import PlatformStore
from ase.testrun import PytestRunner
from ase.verification import PatchVerifier
from ase.workspace import Workspace, WorkspaceManager

IndexLoader = Callable[[Path, str], KnowledgeIndex | None]
SandboxFactory = Callable[[Path], Sandbox]


@dataclass
class AgentServices:
    store: PlatformStore
    llm: LLMClient
    workspaces: WorkspaceManager
    policy: PolicyEngine = field(default_factory=PolicyEngine)
    sandbox_factory: SandboxFactory = field(
        default_factory=lambda: lambda root: build_sandbox(root)
    )
    index_loader: IndexLoader | None = None
    delivery: Delivery | None = None
    python: str | None = None
    clock: Callable[[], float] = time.monotonic
    cleanup_workspaces: bool = True


class AgentNodes:
    def __init__(self, services: AgentServices) -> None:
        self.services = services

    # -- helpers ---------------------------------------------------------------------
    def _run(self, state: AgentState) -> AgentRun:
        run = self.services.store.get(state["run_id"])
        if run is None:
            raise KeyError(state["run_id"])
        return run

    def _save(self, run: AgentRun, new_state: RunState | None = None, **event: Any) -> None:
        if new_state is not None:
            run.state = new_state
        if event:
            kind = str(event.pop("kind"))
            run.record(kind, **event)
        self.services.store.save(run)

    def _record_step(
        self,
        run: AgentRun,
        node: str,
        model: str,
        completion_usage: Any,
        tool_calls: list[ToolCall],
        thought: str,
    ) -> Step:
        step = Step(
            run_id=run.id,
            index=run.steps + 1,
            node=node,
            model=model,
            prompt_tokens=completion_usage.input_tokens + completion_usage.cache_read_tokens,
            output_tokens=completion_usage.output_tokens,
            cached_tokens=completion_usage.cache_read_tokens,
            cost_usd=estimate_cost(model, completion_usage),
            tool_calls=tool_calls,
            thought=thought[:4000],
        )
        self.services.store.add_step(step)
        run.add_step(step)
        return step

    def _elapsed(self, state: AgentState) -> float:
        return self.services.clock() - float(state.get("started_at") or self.services.clock())

    def _workspace(self, state: AgentState) -> Workspace:
        path = state.get("workspace")
        if not path:
            raise RuntimeError("workspace has not been created")
        return Workspace(
            path=Path(path),
            repository=Path(state["repository"]),
            base_sha=state["base_sha"],
            name=Path(path).name,
        )

    # -- nodes -----------------------------------------------------------------------
    def intake(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        task = task_of(state)
        base_sha = task.base_sha or self.services.workspaces.head_sha(Path(state["repository"]))
        run.task = task
        run.base_sha = base_sha
        run.config = config_of(state)
        self._save(
            run, RunState.INGEST_ISSUE, kind="intake", base_sha=base_sha, config=run.config.name
        )
        return {"base_sha": base_sha, "started_at": self.services.clock()}

    def retrieve(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        task = task_of(state)
        config = config_of(state)
        repository = Path(state["repository"])
        index = (
            self.services.index_loader(repository, state["base_sha"])
            if (config.use_index and self.services.index_loader)
            else None
        )
        repo_index = RepositoryIndexer().index(repository)
        if index is not None:
            context = KnowledgeRetriever(index).retrieve(task.issue, repo_index)
            repo_map = index.repo_map(f"{task.issue.title} {task.issue.body}")
        else:
            context = HybridRetriever().retrieve(task.issue, repo_index)
            repo_map = ""
        run.context = context
        self._save(
            run,
            RunState.RETRIEVE_CONTEXT,
            kind="context_retrieved",
            files=[item.path for item in context],
            index="knowledge" if index is not None else "lexical",
        )
        return {"context": [item.model_dump(mode="json") for item in context], "repo_map": repo_map}

    def propose_plan(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        task = task_of(state)
        config = config_of(state)
        request = prompts.plan_request(task, context_of(state), state.get("repo_map", ""))
        completion = self.services.llm.complete(
            model=config.planner_model,
            system=prompts.PLANNER_SYSTEM,
            messages=[Message.user(request)],
            max_tokens=2048,
        )
        self._record_step(run, "plan", config.planner_model, completion.usage, [], completion.text)
        plan, reproduction = prompts.parse_plan(completion.text)
        run.plan = plan
        next_state = (
            RunState.AWAIT_PLAN_APPROVAL
            if self.services.policy.policy.require_plan_approval
            else RunState.CREATE_WORKSPACE
        )
        self._save(run, RunState.PROPOSE_PLAN, kind="plan_proposed", summary=plan.summary)
        self._save(run, next_state)
        return {"plan": plan.model_dump(mode="json"), "reproduction": reproduction}

    def apply_plan_decision(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        decision = state.get("plan_decision") or {"approved": True, "reason": "policy: automatic"}
        approved = bool(decision.get("approved"))
        if run.plan is not None:
            run.plan.approval = Decision.APPROVED if approved else Decision.REJECTED
        self._save(
            run,
            RunState.CREATE_WORKSPACE if approved else RunState.FAILED,
            kind="plan_reviewed",
            approved=approved,
            reason=str(decision.get("reason", "")),
        )
        if approved:
            return {"plan": run.plan.model_dump(mode="json") if run.plan else None}
        run.outcome = "rejected"
        self._save(run)
        return {"outcome": "rejected", "outcome_reason": str(decision.get("reason", ""))}

    def create_workspace(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        task = task_of(state)
        workspace = self.services.workspaces.create(Path(state["repository"]), state["base_sha"])
        notes = list(state.get("notes", []))
        reproduction = state.get("reproduction")
        installed: dict[str, str] = {}
        if reproduction:
            task, note, installed = self._install_reproduction(workspace, task, reproduction)
            notes.append(note)
        if not task.fail_to_pass:
            notes.append("no failing test is known; the whole suite is the acceptance check")
        run.workspace = workspace.path
        run.task = task
        self._save(
            run,
            RunState.IMPLEMENT_PATCH,
            kind="workspace_created",
            path=str(workspace.path),
            fail_to_pass=task.fail_to_pass,
        )
        return {
            "workspace": str(workspace.path),
            "task": task.model_dump(mode="json"),
            "notes": notes,
            "installed_tests": installed,
        }

    def _install_reproduction(
        self, workspace: Workspace, task: Task, reproduction: dict[str, str]
    ) -> tuple[Task, str, dict[str, str]]:
        """Write the planner's reproduction test, keep it only if it fails on the base commit."""
        relative = reproduction["path"].strip().lstrip("/")
        code = reproduction["code"]
        if not looks_like_test(relative) or not relative.endswith(".py"):
            return task, f"reproduction test path rejected: {relative}", {}
        if python_syntax_error(code):
            return task, "reproduction test rejected: it does not parse", {}
        target = workspace.path / relative
        if target.exists():
            return task, f"reproduction test path already exists: {relative}", {}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        runner = PytestRunner(self.services.sandbox_factory(workspace.path), self.services.python)
        _, statuses = runner.run([relative])
        failing = sorted(name for name, status in statuses.items() if status in {"failed", "error"})
        if not failing:
            target.unlink()
            return task, "reproduction test passed on the base commit and was dropped", {}
        updated = task.model_copy(update={"fail_to_pass": [*task.fail_to_pass, *failing]})
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        return updated, f"reproduction test installed: {', '.join(failing)}", {relative: digest}

    def implement(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        task = task_of(state)
        config = config_of(state)
        plan = plan_of(state)
        workspace = self._workspace(state)
        iteration = int(state.get("iteration", 0)) + 1
        run.iteration = iteration
        index = (
            self.services.index_loader(Path(state["repository"]), state["base_sha"])
            if (config.use_index and self.services.index_loader)
            else None
        )
        tools = AgentTools(
            workspace.path,
            self.services.sandbox_factory(workspace.path),
            self.services.policy,
            index=index,
            task_is_tests=bool(task.metadata.get("task_is_tests", False)),
        )
        specs = COMMAND_ONLY_TOOLS if config.tool_mode == "command_only" else STRUCTURED_TOOLS
        request = prompts.coder_request(
            task,
            plan or _fallback_plan(),
            context_of(state),
            state.get("repo_map", ""),
            report_of(state),
            iteration,
            state.get("focus", ""),
        )
        messages: list[Message] = [Message.user(request)]
        notes = list(state.get("notes", []))
        summary = ""
        for _ in range(config.budget.max_tool_calls_per_iteration):
            completion = self.services.llm.complete(
                model=config.coder_model,
                system=prompts.CODER_SYSTEM,
                messages=messages,
                tools=specs,
                max_tokens=4096,
            )
            calls = [tools.dispatch(use.name, use.input) for use in completion.tool_uses]
            self._record_step(
                run, "implement", config.coder_model, completion.usage, calls, completion.text
            )
            self.services.store.save(run)
            exhausted = run.budget_exceeded(self._elapsed(state))
            if not completion.tool_uses:
                summary = completion.text
                break
            messages.append(Message.assistant(completion.content))
            messages.append(
                Message.tool_results(
                    [
                        ToolResultBlock(
                            tool_use_id=use.id,
                            content=call.output or "(no output)",
                            is_error=bool(call.exit_code),
                        )
                        for use, call in zip(completion.tool_uses, calls, strict=True)
                    ]
                )
            )
            if exhausted:
                notes.append(f"iteration {iteration} stopped early: {exhausted}")
                break
        diff = self.services.workspaces.diff(workspace)
        patch = Patch.from_diff(run.id, run.steps, diff)
        self.services.store.add_patch(patch)
        hashes = [*state.get("patch_hashes", []), patch.sha256]
        run.patch = patch
        self._save(
            run,
            RunState.VERIFY_PATCH,
            kind="patch_produced",
            iteration=iteration,
            files=patch.files,
            summary=summary[:500],
        )
        return {
            "iteration": iteration,
            "patch": patch.model_dump(mode="json"),
            "patch_hashes": hashes,
            "notes": notes,
        }

    def verify(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        task = task_of(state)
        patch = patch_of(state)
        workspace = self._workspace(state)
        sandbox = self.services.sandbox_factory(workspace.path)
        runner = PytestRunner(sandbox, self.services.python)
        report = runner.report(run.id, run.steps, task.fail_to_pass, task.pass_to_pass)
        # Regressions: when no pass-to-pass tests are known, run the whole suite once the
        # targeted tests are green so a fix cannot break its neighbours silently.
        if report.green and task.fail_to_pass and not task.pass_to_pass:
            full = runner.report(run.id, run.steps, [], [])
            report = report.model_copy(
                update={
                    "failed": full.failed,
                    "errors": full.errors,
                    "stdout_tail": full.stdout_tail,
                    "total": full.total,
                    "passed": full.passed,
                    "skipped": full.skipped,
                }
            )
        self.services.store.add_test_report(report)
        checks = [
            CheckResult(
                name="tests",
                passed=report.green,
                details=f"{report.passed} passed, {report.failed} failed, {report.errors} errors",
            )
        ]
        files = patch.files if patch else []
        scope = self.services.policy.changed_files(files)
        checks.append(
            CheckResult(
                name="patch-policy",
                passed=scope.allowed,
                details="; ".join(scope.reasons) or "patch scope accepted",
            )
        )
        integrity = PatchVerifier.test_integrity(patch.diff if patch else "")
        installed = state.get("installed_tests", {})
        agent_test_edits = [
            path for path in files if looks_like_test(path) and path not in installed
        ]
        tampered = [
            path
            for path, digest in installed.items()
            if not (workspace.path / path).is_file()
            or hashlib.sha256((workspace.path / path).read_bytes()).hexdigest() != digest
        ]
        if agent_test_edits and not task.metadata.get("task_is_tests"):
            integrity = CheckResult(
                name="test-integrity",
                passed=False,
                details="patch modifies test files, which this task does not allow: "
                + ", ".join(agent_test_edits),
            )
        elif tampered:
            integrity = CheckResult(
                name="test-integrity",
                passed=False,
                details="reproduction test was modified or removed: " + ", ".join(tampered),
            )
        checks.append(integrity)
        evaluation = EvaluationReport(
            passed=all(check.passed for check in checks),
            checks=checks,
            changed_files=files,
            policy_violations=[c.details for c in checks if not c.passed and c.name != "tests"],
        )
        run.test_report = report
        run.evaluation = evaluation
        self._save(
            run,
            RunState.VERIFY_PATCH,
            kind="patch_verified",
            green=report.green,
            passed=evaluation.passed,
            violations=evaluation.policy_violations,
        )
        return {"report": report.model_dump(mode="json")}

    def reflect(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        config = config_of(state)
        report = report_of(state)
        patch = patch_of(state)
        evaluation = run.evaluation
        iteration = int(state.get("iteration", 0))
        hashes = state.get("patch_hashes", [])
        repeated = len(hashes) >= 2 and hashes[-1] == hashes[-2]
        budget_note = run.budget_exceeded(self._elapsed(state))
        files = patch.files if patch else []

        if report is not None and evaluation is not None and evaluation.passed:
            decision, reason, focus = "submit", "tests green and policy checks passed", ""
        elif budget_note:
            decision, reason, focus = "give_up", f"budget exhausted: {budget_note}", ""
        elif repeated:
            decision, reason, focus = (
                "give_up",
                "two identical patches in a row: the plan is wrong, not the edit",
                "",
            )
        elif report is None:
            decision, reason, focus = "give_up", "no test report", ""
        else:
            completion = self.services.llm.complete(
                model=config.classifier_model,
                system=prompts.REFLECT_SYSTEM,
                messages=[
                    Message.user(
                        prompts.reflect_request(report, iteration, budget_note, files, repeated)
                    )
                ],
                max_tokens=512,
            )
            self._record_step(
                run, "reflect", config.classifier_model, completion.usage, [], completion.text
            )
            decision, reason, focus = prompts.parse_reflection(completion.text)
            if decision == "submit":
                # A red run can never be promoted by the model's opinion.
                decision, reason = "retry", "model asked to submit a red run; retrying instead"
            if evaluation is not None and evaluation.policy_violations:
                focus = "; ".join(evaluation.policy_violations) + (f"; {focus}" if focus else "")
        update: dict[str, Any] = {"decision": decision, "focus": focus}
        if decision == "submit":
            next_state = (
                RunState.AWAIT_PR_APPROVAL
                if self.services.policy.policy.require_pr_approval
                else RunState.OPEN_DRAFT_PR
            )
            self._save(
                run, next_state, kind="reflected", decision=decision, reason=reason, focus=focus
            )
        elif decision == "give_up":
            run.outcome = "gave_up"
            self._save(
                run,
                RunState.FAILED,
                kind="reflected",
                decision=decision,
                reason=reason,
                focus=focus,
            )
            self._cleanup(state)
            update.update({"outcome": "gave_up", "outcome_reason": reason})
        else:
            self._save(
                run,
                RunState.IMPLEMENT_PATCH,
                kind="reflected",
                decision=decision,
                reason=reason,
                focus=focus,
            )
        return update

    def apply_pr_decision(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        decision = state.get("pr_decision") or {"approved": True, "reason": "policy: automatic"}
        approved = bool(decision.get("approved"))
        self._save(
            run,
            RunState.OPEN_DRAFT_PR if approved else RunState.FAILED,
            kind="pr_reviewed",
            approved=approved,
            reason=str(decision.get("reason", "")),
        )
        if approved:
            return {}
        run.outcome = "rejected"
        self._save(run)
        self._cleanup(state)
        return {"outcome": "rejected", "outcome_reason": str(decision.get("reason", ""))}

    def open_draft_pr(self, state: AgentState) -> dict[str, Any]:
        run = self._run(state)
        task = task_of(state)
        patch = patch_of(state)
        workspace = self._workspace(state)
        authority = self.services.policy.authority("open_draft_pull_request")
        if not authority.allowed:
            run.outcome = "failed"
            self._save(run, RunState.FAILED, kind="delivery_denied", reasons=authority.reasons)
            return {"outcome": "failed", "outcome_reason": "; ".join(authority.reasons)}
        body = render_agent_pr_body(run, self.services.store.steps(run.id), state.get("notes", []))
        branch = branch_name(run.id, task.issue.number)
        title = sanitize_title(task.issue.title, task.issue.number)
        remote = state.get("remote") or task.issue.repository
        pr_url: str | None = None
        pr_number: int | None = None
        if self.services.delivery is not None and patch is not None and patch.files:
            published = self.services.delivery.publish(
                remote,
                state["base_sha"],
                branch,
                workspace.path,
                patch.files,
                f"fix: {task.issue.title[:60]} (#{task.issue.number})\n\nAgent run {run.id}",
                title,
                body,
            )
            pr_url, pr_number = published.pr_url, published.pr_number
        run.pr_url = pr_url
        run.pr_number = pr_number
        run.outcome = "submitted"
        self._save(run, RunState.COMPLETED, kind="draft_pr_opened", url=pr_url, branch=branch)
        self._cleanup(state)
        return {"outcome": "submitted", "pr_url": pr_url, "pr_number": pr_number}

    def _cleanup(self, state: AgentState) -> None:
        if self.services.cleanup_workspaces and state.get("workspace"):
            # Cleanup must never mask the run outcome.
            with contextlib.suppress(Exception):
                self.services.workspaces.remove(self._workspace(state))


def route_after_reflect(state: AgentState) -> str:
    decision = state.get("decision")
    if decision == "submit":
        return "await_pr_approval"
    if decision == "retry":
        return "implement"
    return "end"


def route_after_plan_decision(state: AgentState) -> str:
    return "end" if state.get("outcome") == "rejected" else "create_workspace"


def route_after_pr_decision(state: AgentState) -> str:
    return "end" if state.get("outcome") == "rejected" else "open_draft_pr"


def _fallback_plan() -> Any:
    from ase.contracts import ChangePlan, PlanStep

    return ChangePlan(
        summary="Resolve the issue with the smallest correct change",
        steps=[PlanStep(description="Implement the fix", files=[], risk="medium")],
    )


__all__ = [
    "AgentNodes",
    "AgentServices",
    "TestReport",
    "route_after_plan_decision",
    "route_after_pr_decision",
    "route_after_reflect",
]
