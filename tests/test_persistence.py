from pathlib import Path

from ase.contracts import AgentRun, Issue
from ase.persistence import SQLiteDatabase, SQLiteRunStore, TaskQueue


def test_run_store_round_trip(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "ase.db")
    store = SQLiteRunStore(database)
    run = AgentRun(issue=Issue(repository="owner/repo", number=1, title="Bug"))
    store.save(run)
    assert store.get(run.id) == run
    assert store.list()[0].id == run.id


def test_queue_claim_complete_and_delivery_idempotency(tmp_path: Path) -> None:
    queue = TaskQueue(SQLiteDatabase(tmp_path / "queue.db"))
    task_id = queue.enqueue("run_1", "analyze", "{}")
    task = queue.claim()
    assert task and task["id"] == task_id
    queue.complete(task_id, True)
    assert queue.claim() is None
    assert queue.record_delivery("delivery-1", "issues")
    assert not queue.record_delivery("delivery-1", "issues")
