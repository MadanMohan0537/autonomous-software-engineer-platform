"""FastAPI control plane and embedded review console."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ase.contracts import Issue
from ase.orchestrator import InvalidTransition, Orchestrator

app = FastAPI(
    title="Autonomous Software Engineer Platform",
    version="0.1.0",
    description="Governed issue-to-draft-PR control plane",
)
orchestrator = Orchestrator()
STATIC = Path(__file__).parent / "static"


class AnalyzeRequest(BaseModel):
    repository_path: str


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str = ""


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/")
def console() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/runs", status_code=201)
def create_run(issue: Issue) -> dict[str, object]:
    return orchestrator.create(issue).model_dump(mode="json")


@app.get("/api/runs")
def list_runs() -> list[dict[str, object]]:
    return [run.model_dump(mode="json") for run in orchestrator.store.list()]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    run = orchestrator.store.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run.model_dump(mode="json")


@app.post("/api/runs/{run_id}/analyze")
async def analyze(run_id: str, request: AnalyzeRequest) -> dict[str, object]:
    root = Path(request.repository_path).resolve()
    allowed_root = Path(os.environ.get("ASE_REPOSITORY_ROOT", Path.cwd())).resolve()
    if root != allowed_root and allowed_root not in root.parents:
        raise HTTPException(403, "repository path is outside ASE_REPOSITORY_ROOT")
    if not root.is_dir():
        raise HTTPException(400, "repository path does not exist")
    try:
        run = await orchestrator.analyze(run_id, root)
    except KeyError as exc:
        raise HTTPException(404, "run not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    return run.model_dump(mode="json")


@app.post("/api/runs/{run_id}/plan-review")
def review_plan(run_id: str, request: ApprovalRequest) -> dict[str, object]:
    try:
        run = orchestrator.approve_plan(run_id, request.approved, request.reason)
    except KeyError as exc:
        raise HTTPException(404, "run not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    return run.model_dump(mode="json")


@app.post("/api/runs/{run_id}/pr-review")
def review_pr(run_id: str, request: ApprovalRequest) -> dict[str, object]:
    try:
        run = orchestrator.approve_pr(run_id, request.approved, request.reason)
    except KeyError as exc:
        raise HTTPException(404, "run not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    return run.model_dump(mode="json")


@app.get("/api/runs/{run_id}/trace")
def trace(run_id: str) -> dict[str, object]:
    """Every model step, patch, test report and review the run produced."""
    if orchestrator.store.get(run_id) is None:
        raise HTTPException(404, "run not found")
    store = orchestrator.store
    return {
        "steps": [item.model_dump(mode="json") for item in store.steps(run_id)],
        "patches": [item.model_dump(mode="json") for item in store.patches(run_id)],
        "test_reports": [item.model_dump(mode="json") for item in store.test_reports(run_id)],
        "reviews": [item.model_dump(mode="json") for item in store.reviews(run_id)],
    }
