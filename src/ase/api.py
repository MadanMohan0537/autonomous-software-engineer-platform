"""FastAPI control plane and embedded review console."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ase.contracts import Issue
from ase.feedback import FeedbackStore, ReviewFeedback
from ase.ide import IDEWorkspace, WorkspacePathError
from ase.orchestrator import InvalidTransition, Orchestrator
from ase.persistence import SQLiteDatabase, SQLiteRunStore, TaskQueue
from ase.webhooks import InvalidSignature, decode_issue_event, verify_signature

app = FastAPI(
    title="Autonomous Software Engineer Platform",
    version="0.2.0",
    description="Governed issue-to-draft-PR control plane",
)
database = SQLiteDatabase(Path(os.environ.get("ASE_DATABASE_PATH", ".ase/ase.db")))
orchestrator = Orchestrator(store=SQLiteRunStore(database))
task_queue = TaskQueue(database)
feedback_store = FeedbackStore(Path(os.environ.get("ASE_FEEDBACK_PATH", ".ase/feedback.db")))
STATIC = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class AnalyzeRequest(BaseModel):
    repository_path: str


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str = ""


class RecipeRequest(BaseModel):
    recipe: str


def ide_workspace() -> IDEWorkspace:
    root = Path(os.environ.get("ASE_REPOSITORY_ROOT", Path.cwd())).resolve()
    return IDEWorkspace(root)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.2.0"}


@app.get("/")
def console() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/ide/tree")
def ide_tree(path: str = "") -> list[dict[str, object]]:
    try:
        return [entry.__dict__ for entry in ide_workspace().tree(path)]
    except WorkspacePathError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/ide/file")
def ide_file(path: str) -> dict[str, str]:
    try:
        return {"path": path, "content": ide_workspace().read(path)}
    except WorkspacePathError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/ide/diff")
def ide_diff(path: str | None = None) -> dict[str, str]:
    try:
        return {"diff": ide_workspace().diff(path)}
    except WorkspacePathError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/ide/search")
def ide_search(query: str) -> list[dict[str, object]]:
    try:
        return [hit.__dict__ for hit in ide_workspace().search(query)]
    except WorkspacePathError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/ide/run")
def ide_run(request: RecipeRequest) -> dict[str, object]:
    try:
        return ide_workspace().run_recipe(request.recipe).model_dump(mode="json")
    except WorkspacePathError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    """The second human gate: a verified patch still needs a named approval to become a PR."""
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


@app.post("/api/runs/{run_id}/feedback", status_code=201)
def add_feedback(run_id: str, feedback: ReviewFeedback) -> dict[str, int]:
    if feedback.run_id != run_id:
        raise HTTPException(400, "run ID does not match request path")
    if orchestrator.store.get(run_id) is None:
        raise HTTPException(404, "run not found")
    return {"id": feedback_store.append(feedback)}


@app.get("/api/runs/{run_id}/feedback")
def list_feedback(run_id: str) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in feedback_store.for_run(run_id)]


@app.post("/api/github/webhook", status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(alias="X-GitHub-Event"),
    x_github_delivery: str = Header(alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, object]:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "GitHub webhook secret is not configured")
    body = await request.body()
    try:
        verify_signature(body, x_hub_signature_256, secret)
    except InvalidSignature as exc:
        raise HTTPException(401, str(exc)) from exc
    if not task_queue.record_delivery(x_github_delivery, x_github_event):
        return {"accepted": False, "reason": "duplicate delivery"}
    if x_github_event != "issues":
        return {"accepted": False, "reason": "event ignored"}
    event = decode_issue_event(json.loads(body))
    if event.action not in {"opened", "labeled"} or "ase:ready" not in event.labels:
        return {"accepted": False, "reason": "issue is not ready"}
    run = orchestrator.create(
        Issue(
            repository=event.repository,
            number=event.number,
            title=event.title,
            body=event.body,
            labels=event.labels,
        )
    )
    task_id = task_queue.enqueue(
        run.id,
        "analyze",
        json.dumps({"issue": run.issue.model_dump(), "path": event.repository.split("/")[-1]}),
    )
    return {"accepted": True, "run_id": run.id, "task_id": task_id}
