"""Prompts for the planner, the coder and the reflection step.

Kept in one file so the whole conversation the model sees can be read top to bottom.
The prompts state the rules the platform also enforces in code (no test edits, no
skips, argument arrays only); a model that follows them wastes fewer iterations.
"""

from __future__ import annotations

import json
from typing import Any

from ase.contracts import ChangePlan, ContextItem, PlanStep, Task, TestReport

PLANNER_SYSTEM = """You are the planning stage of a governed software-engineering agent.
You read a GitHub issue plus retrieved context from the repository and produce a short,
bounded plan. You never edit code. Reproduce first: whenever the issue describes observable
behaviour, write ONE pytest test function that fails today and passes once the issue is fixed.

Respond with a single JSON object and nothing else:
{
  "summary": "one sentence describing the change",
  "steps": [{"description": "...", "files": ["path/one.py"], "risk": "low|medium|high"}],
  "assumptions": ["..."],
  "reproduction_test": {"path": "tests/test_reproduce_issue.py",
                        "code": "import ...\\n\\ndef test_...():\\n    ..."}
}
Set "reproduction_test" to null when the issue cannot be reproduced with a unit test.
The test must import from the repository's own modules and must not skip or xfail."""

CODER_SYSTEM = """You are the implementation stage of a governed software-engineering agent working
inside an isolated checkout of a repository. Your job is to make the failing tests pass with the
smallest correct change, without weakening any test.

Rules the platform enforces (violations are rejected automatically, so do not try them):
- Edit files only through the edit_file tool; `search` must match the file verbatim.
- Do not edit or delete tests, add skips, xfails or `.only`, or loosen assertions.
- Commands are argument arrays for allowlisted programs (python, pytest, ruff, mypy, git read-only).
  There is no shell and no network.
- Stay inside the plan's files unless the code proves the plan wrong; say so if it does.

Work like an engineer: read before editing, run the relevant tests after editing, and stop
when the failing tests pass. When you are done, reply with a short summary of what you changed
and why, with no further tool calls."""

REFLECT_SYSTEM = """You review one iteration of an automated code change and decide what to do next.
Reply with a JSON object only: {"decision": "submit" | "retry" | "give_up", "reason": "...",
"next_focus": "what the next iteration should concentrate on, if retrying"}."""


def plan_request(task: Task, context: list[ContextItem], repo_map: str) -> str:
    issue = task.issue
    files = "\n".join(
        f"- {item.path} (score {item.score}; {item.reason}; "
        f"symbols: {', '.join(item.symbols) or '-'})"
        for item in context
    )
    known_tests = ", ".join(task.fail_to_pass) or "none provided"
    return (
        f"# Issue #{issue.number}: {issue.title}\n\n{issue.body.strip() or '(no body)'}\n\n"
        f"Labels: {', '.join(issue.labels) or '-'}\nTask source: {task.source.value}\n"
        f"Known failing tests: {known_tests}\n\n"
        f"# Retrieved context (most relevant first)\n{files or '- none'}\n\n"
        f"# Repository map (ranked toward the issue)\n{repo_map or '(empty)'}\n"
    )


def parse_plan(text: str) -> tuple[ChangePlan, dict[str, Any] | None]:
    """Parse the planner's JSON. Tolerates code fences and prose around the object."""
    payload = _extract_json(text)
    steps = payload.get("steps") or []
    plan = ChangePlan(
        summary=str(payload.get("summary") or "Resolve the issue"),
        steps=[
            PlanStep(
                description=str(step.get("description", "")),
                files=[str(item) for item in step.get("files", [])],
                risk=str(step.get("risk", "low")),
            )
            for step in steps
            if isinstance(step, dict)
        ]
        or [PlanStep(description="Implement the smallest supported change", risk="medium")],
        assumptions=[str(item) for item in payload.get("assumptions", [])],
    )
    reproduction = payload.get("reproduction_test")
    if isinstance(reproduction, dict) and reproduction.get("code") and reproduction.get("path"):
        return plan, {"path": str(reproduction["path"]), "code": str(reproduction["code"])}
    return plan, None


def coder_request(
    task: Task,
    plan: ChangePlan,
    context: list[ContextItem],
    repo_map: str,
    report: TestReport | None,
    iteration: int,
    focus: str,
) -> str:
    issue = task.issue
    steps = "\n".join(
        f"{index}. {step.description} (files: {', '.join(step.files) or '-'}; risk {step.risk})"
        for index, step in enumerate(plan.steps, start=1)
    )
    failing = [name for name, ok in (report.fail_to_pass.items() if report else []) if not ok]
    regressions = report.regressions if report else []
    last_output = report.stdout_tail if report else ""
    sections = [
        f"# Issue #{issue.number}: {issue.title}\n\n{issue.body.strip() or '(no body)'}",
        f"# Approved plan\n{plan.summary}\n{steps}",
        "# Tests that must pass\n"
        + (", ".join(task.fail_to_pass) or "(run the suite around the files you touch)"),
        "# Relevant files\n" + "\n".join(f"- {item.path}" for item in context[:8]),
        f"# Repository map\n{repo_map or '(empty)'}",
    ]
    if iteration > 1:
        sections.append(
            f"# Iteration {iteration}\nStill failing: {', '.join(failing) or '-'}\n"
            f"Regressions: {', '.join(regressions) or '-'}\nFocus: {focus or '-'}\n"
            f"Last test output (tail):\n```\n{last_output[-3000:]}\n```"
        )
    return "\n\n".join(sections)


def reflect_request(
    report: TestReport,
    iteration: int,
    budget_note: str | None,
    patch_files: list[str],
    repeated: bool,
) -> str:
    failing = [name for name, ok in report.fail_to_pass.items() if not ok]
    return json.dumps(
        {
            "iteration": iteration,
            "green": report.green,
            "still_failing": failing,
            "regressions": report.regressions,
            "totals": {"passed": report.passed, "failed": report.failed, "errors": report.errors},
            "patch_files": patch_files,
            "identical_to_previous_patch": repeated,
            "budget_exhausted": budget_note,
            "test_output_tail": report.stdout_tail[-2000:],
        },
        indent=2,
    )


def parse_reflection(text: str) -> tuple[str, str, str]:
    payload = _extract_json(text)
    decision = str(payload.get("decision") or "retry").lower()
    if decision not in {"submit", "retry", "give_up"}:
        decision = "retry"
    return decision, str(payload.get("reason") or ""), str(payload.get("next_focus") or "")


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
