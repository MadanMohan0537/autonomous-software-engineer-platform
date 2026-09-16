import json
from pathlib import Path

import pytest

from ase.config import Settings
from ase.persistence import SQLiteDatabase, TaskQueue
from ase.worker import run_once


@pytest.mark.asyncio
async def test_worker_analyzes_queued_repository(tmp_path: Path) -> None:
    repository = tmp_path / "demo"
    repository.mkdir()
    (repository / "bug.py").write_text("def failure(): pass\n", encoding="utf-8")
    database_path = tmp_path / "ase.db"
    queue = TaskQueue(SQLiteDatabase(database_path))
    queue.enqueue(
        "external-run-id",
        "analyze",
        json.dumps(
            {
                "path": "demo",
                "issue": {"repository": "owner/demo", "number": 1, "title": "Failure"},
            }
        ),
    )
    settings = Settings(
        repository_root=tmp_path,
        database_path=database_path,
        model_endpoint=None,
        model_name="none",
        model_api_key=None,
        github_token=None,
        github_webhook_secret=None,
        execution_backend="local",
        network_enabled=False,
    )
    assert await run_once(settings)
    assert not await run_once(settings)


@pytest.mark.asyncio
async def test_worker_marks_unknown_task_failed(tmp_path: Path) -> None:
    database_path = tmp_path / "ase.db"
    TaskQueue(SQLiteDatabase(database_path)).enqueue("run", "unknown")
    settings = Settings(
        repository_root=tmp_path,
        database_path=database_path,
        model_endpoint=None,
        model_name="none",
        model_api_key=None,
        github_token=None,
        github_webhook_secret=None,
        execution_backend="local",
        network_enabled=False,
    )
    with pytest.raises(ValueError):
        await run_once(settings)
