import hashlib
import hmac
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from ase.api import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200


def test_create_and_get_run() -> None:
    response = client.post(
        "/api/runs",
        json={"repository": "owner/repo", "number": 12, "title": "Repair parser"},
    )
    assert response.status_code == 201
    run_id = response.json()["id"]
    assert client.get(f"/api/runs/{run_id}").status_code == 200
    assert client.get("/api/runs").status_code == 200
    assert client.get("/").status_code == 200


def test_missing_run_is_404() -> None:
    assert client.get("/api/runs/missing").status_code == 404


def test_plan_review_before_analysis_conflicts() -> None:
    response = client.post(
        "/api/runs",
        json={"repository": "owner/repo", "number": 14, "title": "Repair parser"},
    )
    denied = client.post(
        f"/api/runs/{response.json()['id']}/plan-review",
        json={"approved": True, "reason": "reviewed"},
    )
    assert denied.status_code == 409


def test_analyze_rejects_outside_root(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("ASE_REPOSITORY_ROOT", str(tmp_path))  # type: ignore[attr-defined]
    response = client.post(
        "/api/runs",
        json={"repository": "owner/repo", "number": 13, "title": "Repair parser"},
    )
    run_id = response.json()["id"]
    denied = client.post(f"/api/runs/{run_id}/analyze", json={"repository_path": "/"})
    assert denied.status_code == 403


def test_feedback_endpoints() -> None:
    created = client.post(
        "/api/runs",
        json={"repository": "owner/repo", "number": 15, "title": "Feedback"},
    ).json()
    payload = {
        "run_id": created["id"],
        "stage": "plan",
        "decision": "approved",
        "reason_codes": [],
    }
    assert client.post(f"/api/runs/{created['id']}/feedback", json=payload).status_code == 201
    assert client.get(f"/api/runs/{created['id']}/feedback").json()[0]["stage"] == "plan"


def test_authenticated_issue_webhook(monkeypatch: object) -> None:
    secret = "webhook-secret"
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", secret)  # type: ignore[attr-defined]
    payload = {
        "action": "opened",
        "repository": {"full_name": "owner/demo"},
        "issue": {
            "number": 16,
            "title": "Webhook bug",
            "body": "Details",
            "labels": [{"name": "ase:ready"}],
        },
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": f"delivery-test-{uuid4().hex}",
            "X-Hub-Signature-256": signature,
        },
    )
    assert response.status_code == 202
    assert response.json()["accepted"]


def test_ide_endpoints(tmp_path: Path, monkeypatch: object) -> None:
    (tmp_path / "app.py").write_text("def searchable(): pass\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setenv("ASE_REPOSITORY_ROOT", str(tmp_path))  # type: ignore[attr-defined]
    tree = client.get("/api/ide/tree")
    assert tree.status_code == 200
    assert any(item["name"] == "app.py" for item in tree.json())
    assert "searchable" in client.get("/api/ide/file", params={"path": "app.py"}).json()["content"]
    assert client.get("/api/ide/search", params={"query": "searchable"}).json()[0]["line"] == 1
    assert client.get("/api/ide/file", params={"path": "../escape"}).status_code == 400


def test_pr_review_gate_and_trace() -> None:
    from ase.api import orchestrator
    from ase.contracts import RunState

    response = client.post(
        "/api/runs",
        json={"repository": "owner/repo", "number": 17, "title": "Repair parser"},
    )
    run_id = response.json()["id"]
    assert client.get(f"/api/runs/{run_id}/trace").json() == {
        "steps": [],
        "patches": [],
        "test_reports": [],
        "reviews": [],
    }
    assert client.get("/api/runs/missing/trace").status_code == 404
    denied = client.post(f"/api/runs/{run_id}/pr-review", json={"approved": True})
    assert denied.status_code == 409
    assert client.post("/api/runs/missing/pr-review", json={"approved": True}).status_code == 404
    run = orchestrator.store.get(run_id)
    assert run is not None
    run.state = RunState.AWAIT_PR_APPROVAL
    orchestrator.store.save(run)
    rejected = client.post(
        f"/api/runs/{run_id}/pr-review", json={"approved": False, "reason": "no"}
    )
    assert rejected.json()["state"] == "failed" and rejected.json()["outcome"] == "rejected"
