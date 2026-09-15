"""Draft pull-request bodies generated from the trace, never from the model's claims."""

from __future__ import annotations

from ase.contracts import AgentRun, Step
from ase.github import render_pr_body


def render_agent_pr_body(run: AgentRun, steps: list[Step], notes: list[str]) -> str:
    plan = run.plan
    report = run.test_report
    patch = run.patch
    checks: list[tuple[str, bool]] = []
    if run.evaluation is not None:
        checks = [(check.name, check.passed) for check in run.evaluation.checks]
    summary = plan.summary if plan else "Automated change"
    base = render_pr_body(run.id, summary, checks)

    plan_lines = "\n".join(
        f"{index}. {step.description} "
        f"({', '.join(step.files) or 'files decided during implementation'})"
        for index, step in enumerate(plan.steps if plan else [], start=1)
    )
    test_lines = ""
    if report is not None:
        f2p = ", ".join(f"`{name}`" for name, ok in report.fail_to_pass.items() if ok) or "-"
        test_lines = (
            f"- Fail-to-pass now passing: {f2p}\n"
            f"- Totals: {report.passed} passed, {report.failed} failed, {report.errors} errors, "
            f"{report.skipped} skipped\n"
            f"- Regressions: {', '.join(report.regressions) or 'none'}"
        )
    tool_calls = sum(len(step.tool_calls) for step in steps)
    provenance = (
        f"- Iterations: {run.iteration}\n"
        f"- Model steps: {run.steps} ({tool_calls} tool calls)\n"
        f"- Tokens: {run.prompt_tokens} in / {run.output_tokens} out, "
        f"estimated ${run.cost_usd:.2f}\n"
        f"- Base commit: `{run.base_sha or '-'}`\n"
        f"- Files changed: {', '.join(patch.files) if patch else '-'}\n"
        f"- Test files touched: {'yes' if patch and patch.touched_tests else 'no'}"
    )
    uncertain = "\n".join(f"- {note}" for note in notes) or "- none recorded"
    assumption_items = plan.assumptions if plan else []
    assumptions = "\n".join(f"- {item}" for item in assumption_items) or "- none"
    return (
        f"{base}\n## Plan\n\n{plan_lines or '-'}\n\n## Tests\n\n{test_lines or '- not run'}\n\n"
        f"## Assumptions\n\n{assumptions}\n\n## What stayed uncertain\n\n{uncertain}\n\n"
        f"## Run provenance\n\n{provenance}\n"
    )
