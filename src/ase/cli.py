"""Command-line interface for local indexing and analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ase.contracts import Issue
from ase.orchestrator import Orchestrator
from ase.repo_intelligence import RepositoryIndexer


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ase")
    commands = root.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Index a repository")
    index.add_argument("repository", type=Path)
    index.add_argument("--output", type=Path)
    analyze = commands.add_parser("analyze", help="Create a plan for a local issue")
    analyze.add_argument("repository", type=Path)
    analyze.add_argument("--issue", type=int, required=True)
    analyze.add_argument("--title", required=True)
    analyze.add_argument("--body", default="")
    return root


async def _analyze(args: argparse.Namespace) -> None:
    orchestrator = Orchestrator()
    run = orchestrator.create(
        Issue(repository=args.repository.name, number=args.issue, title=args.title, body=args.body)
    )
    run = await orchestrator.analyze(run.id, args.repository)
    print(json.dumps(run.model_dump(mode="json"), indent=2))


def main() -> None:
    args = parser().parse_args()
    if args.command == "index":
        index = RepositoryIndexer().index(args.repository)
        if args.output:
            index.write_manifest(args.output)
        print(json.dumps(index.to_dict(), indent=2))
    else:
        asyncio.run(_analyze(args))


if __name__ == "__main__":
    main()
