from pathlib import Path

from fastapi.testclient import TestClient

from ase.api import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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


def test_pr_review_gate_and_trace() -> None:
    from ase.contracts import RunState

    response = client.post(
        "/api/runs",
        json={"repository": "owner/repo", "number": 15, "title": "Repair parser"},
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
    from ase.api import orchestrator

    run = orchestrator.store.get(run_id)
    assert run is not None
    run.state = RunState.AWAIT_PR_APPROVAL
    orchestrator.store.save(run)
    rejected = client.post(
        f"/api/runs/{run_id}/pr-review", json={"approved": False, "reason": "no"}
    )
    assert rejected.json()["state"] == "failed" and rejected.json()["outcome"] == "rejected"
