"""Small durable worker for queued repository-analysis tasks."""

from __future__ import annotations

import argparse
import asyncio
import json

from ase.config import Settings
from ase.contracts import Issue
from ase.orchestrator import Orchestrator
from ase.persistence import SQLiteDatabase, SQLiteRunStore, TaskQueue


async def run_once(settings: Settings) -> bool:
    database = SQLiteDatabase(settings.database_path)
    queue = TaskQueue(database)
    task = queue.claim()
    if task is None:
        return False
    succeeded = False
    try:
        payload = json.loads(task["payload"])
        orchestrator = Orchestrator(store=SQLiteRunStore(database))
        if task["kind"] == "analyze":
            run = orchestrator.store.get(task["run_id"])
            if run is None:
                issue = Issue.model_validate(payload["issue"])
                run = orchestrator.create(issue)
            await orchestrator.analyze(run.id, settings.repository_root / payload["path"])
        else:
            raise ValueError(f"unsupported task kind: {task['kind']}")
        succeeded = True
        return True
    finally:
        queue.complete(int(task["id"]), succeeded)


def main() -> None:
    parser = argparse.ArgumentParser(prog="ase-worker")
    parser.add_argument("--once", action="store_true", help="process one task and exit")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.once:
        asyncio.run(run_once(settings))
        return
    while asyncio.run(run_once(settings)):
        pass


if __name__ == "__main__":
    main()
