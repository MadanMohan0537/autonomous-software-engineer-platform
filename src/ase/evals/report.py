"""Comparison tables: resolved rate, cost per resolved task, steps and wall-clock by config.

The rules from docs/EVALUATION.md apply: case-level results are preserved next to every
aggregate, attempted and resolved are reported separately, and runs on different task
sets are never mixed into one row.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from ase.contracts import EvalResult


class ConfigSummary(BaseModel):
    suite: str
    config_name: str
    attempted: int
    resolved: int
    total_cost_usd: float
    mean_steps: float
    mean_wall_s: float

    @property
    def resolved_rate(self) -> float:
        return round(self.resolved / self.attempted, 4) if self.attempted else 0.0

    @property
    def cost_per_resolved(self) -> float | None:
        return round(self.total_cost_usd / self.resolved, 4) if self.resolved else None


def summarize(results: list[EvalResult]) -> list[ConfigSummary]:
    groups: dict[tuple[str, str], list[EvalResult]] = defaultdict(list)
    for item in results:
        groups[(item.suite, item.config_name)].append(item)
    summaries: list[ConfigSummary] = []
    for (suite, config), items in sorted(groups.items()):
        summaries.append(
            ConfigSummary(
                suite=suite,
                config_name=config,
                attempted=len(items),
                resolved=sum(1 for item in items if item.resolved),
                total_cost_usd=round(sum(item.cost_usd for item in items), 4),
                mean_steps=round(sum(item.steps for item in items) / len(items), 2),
                mean_wall_s=round(sum(item.wall_s for item in items) / len(items), 1),
            )
        )
    return summaries


def render_table(summaries: list[ConfigSummary]) -> str:
    header = (
        "| Suite | Config | Attempted | Resolved | Rate | Total $ | $ / resolved | Steps | "
        "Wall s |\n|---|---|---|---|---|---|---|---|---|"
    )
    rows = [
        f"| {item.suite} | {item.config_name} | {item.attempted} | {item.resolved} | "
        f"{item.resolved_rate:.0%} | {item.total_cost_usd:.2f} | "
        f"{'-' if item.cost_per_resolved is None else f'{item.cost_per_resolved:.2f}'} | "
        f"{item.mean_steps:.1f} | {item.mean_wall_s:.0f} |"
        for item in summaries
    ]
    return "\n".join([header, *rows])


def render_cases(results: list[EvalResult]) -> str:
    lines = [
        "| Suite | Config | Task | Resolved | $ | Steps | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item.suite} | {item.config_name} | {item.task_id} | "
            f"{'yes' if item.resolved else 'no'} | {item.cost_usd:.2f} | {item.steps} | "
            f"{item.notes} |"
        )
    return "\n".join(lines)


def write_results(results: list[EvalResult], directory: Path, suite: str, config: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{suite}--{config}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for item in results:
            handle.write(json.dumps(item.model_dump(mode="json"), sort_keys=True) + "\n")
    return path


def read_results(directory: Path) -> list[EvalResult]:
    results: list[EvalResult] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                results.append(EvalResult.model_validate_json(line))
    return results
