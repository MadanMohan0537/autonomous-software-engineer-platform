"""SWE-bench adapter: instances in, predictions out, grading by the official harness.

The platform never re-implements SWE-bench grading. It turns dataset instances into
tasks, records the agent's patch per instance, and writes the predictions file the
official harness (`python -m swebench.harness.run_evaluation`) consumes inside its own
Docker images. Report the dataset name, split, instance ids, model configuration and
attempt budget next to every number, as docs/EVALUATION.md requires.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ase.evals.suites import EvalTask, Suite


class Prediction(BaseModel):
    instance_id: str
    model_name_or_path: str
    model_patch: str


def _listish(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def instance_to_task(instance: dict[str, Any]) -> EvalTask:
    """Map one SWE-bench instance (JSON or JSONL row) to an EvalTask.

    `repository` is the GitHub `owner/name`; cloning it at `base_commit` is the caller's
    job (or the harness's), so the task carries the metadata rather than a local path.
    """
    return EvalTask(
        id=str(instance["instance_id"]),
        repository=str(instance.get("repo", "")),
        base_sha=str(instance.get("base_commit") or "") or None,
        title=str(instance.get("problem_statement", ""))[:120].splitlines()[0]
        if instance.get("problem_statement")
        else str(instance["instance_id"]),
        body=str(instance.get("problem_statement", "")),
        fail_to_pass=_listish(instance.get("FAIL_TO_PASS")),
        pass_to_pass=_listish(instance.get("PASS_TO_PASS")),
        metadata={
            "version": instance.get("version"),
            "created_at": instance.get("created_at"),
            "hints": instance.get("hints_text", ""),
            "test_patch": instance.get("test_patch", ""),
        },
    )


def load_instances(path: Path, limit: int | None = None) -> Suite:
    """Load `.json` (list) or `.jsonl` instances exported from the SWE-bench dataset."""
    text = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]]
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        loaded = json.loads(text)
        rows = list(loaded) if isinstance(loaded, list) else [loaded]
    if limit is not None:
        rows = rows[:limit]
    return Suite(
        name=f"swebench-{path.stem}",
        description=f"{len(rows)} SWE-bench instances from {path.name}",
        tasks=[instance_to_task(row) for row in rows],
    )


class PredictionsFile(BaseModel):
    path: Path
    predictions: list[Prediction] = Field(default_factory=list)

    def add(self, instance_id: str, model_name: str, patch: str) -> None:
        self.predictions.append(
            Prediction(instance_id=instance_id, model_name_or_path=model_name, model_patch=patch)
        )

    def write(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for item in self.predictions:
                handle.write(json.dumps(item.model_dump()) + "\n")
        return self.path


def harness_command(
    predictions: Path,
    dataset: str = "princeton-nlp/SWE-bench_Lite",
    run_id: str = "ase",
    workers: int = 4,
) -> list[str]:
    """The official evaluation command. Requires Docker and the `swebench` package."""
    return [
        "python",
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        dataset,
        "--predictions_path",
        str(predictions),
        "--max_workers",
        str(workers),
        "--run_id",
        run_id,
    ]
