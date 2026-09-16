"""Command-line interface.

    ase index REPO [--output PATH]              lightweight manifest (files, symbols, edges)
    ase analyze REPO --issue N --title T        plan an issue and stop at the approval gate
    ase knowledge build|search|eval REPO ...    the SHA-keyed knowledge index
    ase run REPO --issue N --title T [...]      the full issue-to-draft-PR workflow
    ase resume RUN_ID --approve|--reject        resume a checkpointed (LangGraph) run
    ase eval harvest|run|report ...             evaluation suites, runs and tables
    ase mutate REPO --paths ...                 mutation testing on chosen files
    ase gen-tests REPO --paths ...              coverage-guided, mutation-validated tests
    ase ci check RUN_ID                         triage the CI run of a draft PR
    ase reviews sync                            mirror PR reviews into the trace store
    ase feedback dataset|train|agreement ...    labels, the logistic scorer, agreement
    ase canary simulate WINDOWS.json            progressive-delivery decisions (no deploy)

Every command that talks to a model or to GitHub reads credentials from the environment
(see .env.example) and works in dry-run mode without them wherever that is meaningful.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ase.agent.runner import ApprovalGate, AutoApprove, CallbackGate
from ase.agent.service import AgentService, load_or_build_index
from ase.agent.state import AgentState
from ase.config import Settings
from ase.contracts import Issue, RunConfig, ToolMode
from ase.orchestrator import Orchestrator
from ase.repo_intelligence import RepositoryIndexer
from ase.repo_intelligence.embeddings import select_embedder
from ase.repo_intelligence.retrieval_eval import cases_from_git_log, evaluate_retrieval
from ase.sandbox import build_sandbox
from ase.workspace import WorkspaceManager

Printer = Callable[[str], None]


def make_service(settings: Settings, repository: Path) -> AgentService:
    """Factory for the agent service; tests replace it to inject a scripted model."""
    return AgentService(settings, repository)


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

    knowledge = commands.add_parser(
        "knowledge", help="Build, query or evaluate the knowledge index"
    )
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command", required=True)
    build = knowledge_commands.add_parser("build")
    build.add_argument("repository", type=Path)
    search = knowledge_commands.add_parser("search")
    search.add_argument("repository", type=Path)
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)
    evaluate = knowledge_commands.add_parser("eval")
    evaluate.add_argument("repository", type=Path)
    evaluate.add_argument("--limit", type=int, default=10)
    evaluate.add_argument("-k", type=int, default=10)

    run = commands.add_parser("run", help="Run the issue-to-draft-PR workflow")
    run.add_argument("repository", type=Path)
    run.add_argument("--issue", type=int, required=True)
    run.add_argument("--title", required=True)
    run.add_argument("--body", default="")
    run.add_argument("--remote", help="owner/name on GitHub for delivery")
    run.add_argument("--config", default="structured")
    run.add_argument("--tool-mode", choices=["structured", "command_only"], default="structured")
    run.add_argument("--no-index", action="store_true")
    run.add_argument("--auto-approve", action="store_true", help="approve both gates automatically")
    run.add_argument("--graph", action="store_true", help="use the checkpointed LangGraph workflow")

    resume = commands.add_parser("resume", help="Resume a checkpointed run at a gate")
    resume.add_argument("repository", type=Path)
    resume.add_argument("run_id")
    decision = resume.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    resume.add_argument("--reason", default="")

    evals = commands.add_parser("eval", help="Evaluation suites, runs and reports")
    eval_commands = evals.add_subparsers(dest="eval_command", required=True)
    harvest = eval_commands.add_parser("harvest")
    harvest.add_argument("repository", type=Path)
    harvest.add_argument("--limit", type=int, default=5)
    harvest.add_argument("--output", type=Path, required=True)
    eval_run = eval_commands.add_parser("run")
    eval_run.add_argument("repository", type=Path)
    eval_run.add_argument("--suite", type=Path, required=True)
    eval_run.add_argument("--config", default="structured")
    eval_run.add_argument(
        "--tool-mode", choices=["structured", "command_only"], default="structured"
    )
    eval_run.add_argument("--no-index", action="store_true")
    eval_run.add_argument("--results-dir", type=Path, default=Path("evals/results"))
    eval_report = eval_commands.add_parser("report")
    eval_report.add_argument("--results-dir", type=Path, default=Path("evals/results"))
    eval_report.add_argument("--cases", action="store_true")

    mutate = commands.add_parser("mutate", help="Mutation testing on chosen files")
    mutate.add_argument("repository", type=Path)
    mutate.add_argument("--paths", nargs="+", required=True)
    mutate.add_argument("--tests", nargs="*", default=[])
    mutate.add_argument("--budget", type=int, default=600)

    gen = commands.add_parser("gen-tests", help="Generate tests validated by mutation testing")
    gen.add_argument("repository", type=Path)
    gen.add_argument("--paths", nargs="+", required=True)
    gen.add_argument("--sources", nargs="*", default=[])

    ci = commands.add_parser("ci", help="CI triage for draft pull requests")
    ci_commands = ci.add_subparsers(dest="ci_command", required=True)
    ci_check = ci_commands.add_parser("check")
    ci_check.add_argument("run_id")

    reviews = commands.add_parser("reviews", help="Mirror pull-request reviews")
    reviews_commands = reviews.add_subparsers(dest="reviews_command", required=True)
    reviews_commands.add_parser("sync")

    feedback = commands.add_parser("feedback", help="Labels, scorers and agreement")
    feedback_commands = feedback.add_subparsers(dest="feedback_command", required=True)
    dataset = feedback_commands.add_parser("dataset")
    dataset.add_argument("--output", type=Path, required=True)
    train = feedback_commands.add_parser("train")
    train.add_argument("--dataset", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    agree = feedback_commands.add_parser("agreement")
    agree.add_argument("--dataset", type=Path, required=True)
    agree.add_argument("--model", type=Path, help="logistic scorer json; heuristic when omitted")

    canary = commands.add_parser("canary", help="Progressive-delivery decisions")
    canary_commands = canary.add_subparsers(dest="canary_command", required=True)
    simulate = canary_commands.add_parser("simulate")
    simulate.add_argument("windows", type=Path, help="JSON list of {requests, errors, p95_ms}")
    simulate.add_argument("--image", default="demo:latest")
    return root


# -- helpers -----------------------------------------------------------------------------


def _settings() -> Settings:
    return Settings.from_env()


def _config(settings: Settings, name: str, tool_mode: str, use_index: bool) -> RunConfig:
    mode: ToolMode = "command_only" if tool_mode == "command_only" else "structured"
    config = settings.run_config(name=name, tool_mode=mode)
    return config.model_copy(update={"use_index": use_index})


def _terminal_gate(out: Printer) -> ApprovalGate:
    def ask(kind: str, state: AgentState) -> dict[str, Any]:
        if kind == "plan":
            out(json.dumps(state.get("plan"), indent=2))
        else:
            out(json.dumps(state.get("report"), indent=2))
        answer = input(f"approve {kind}? [y/N] ").strip().lower()
        return {"approved": answer in {"y", "yes"}, "reason": "terminal decision"}

    return CallbackGate(ask)


def _print_state(state: AgentState, out: Printer) -> None:
    out(
        json.dumps(
            {
                "run_id": state.get("run_id"),
                "outcome": state.get("outcome"),
                "reason": state.get("outcome_reason"),
                "pr_url": state.get("pr_url"),
                "iterations": state.get("iteration"),
                "notes": state.get("notes"),
            },
            indent=2,
        )
    )


# -- command handlers ----------------------------------------------------------------------


def cmd_index(args: argparse.Namespace, out: Printer) -> int:
    index = RepositoryIndexer().index(args.repository)
    if args.output:
        index.write_manifest(args.output)
    out(json.dumps(index.to_dict(), indent=2))
    return 0


async def _analyze(args: argparse.Namespace, out: Printer) -> None:
    orchestrator = Orchestrator()
    run = orchestrator.create(
        Issue(repository=args.repository.name, number=args.issue, title=args.title, body=args.body)
    )
    run = await orchestrator.analyze(run.id, args.repository)
    out(json.dumps(run.model_dump(mode="json"), indent=2))


def cmd_analyze(args: argparse.Namespace, out: Printer) -> int:
    asyncio.run(_analyze(args, out))
    return 0


def cmd_knowledge(args: argparse.Namespace, out: Printer) -> int:
    settings = _settings()
    repository = args.repository.resolve()
    sha = WorkspaceManager(settings.workspaces_dir).head_sha(repository)
    embedder = select_embedder(settings.voyage_api_key, settings.voyage_model)
    index = load_or_build_index(repository, sha, settings.index_dir, embedder)
    if args.knowledge_command == "build":
        out(index.meta.model_dump_json(indent=2))
    elif args.knowledge_command == "search":
        for hit in index.search(args.query, k=args.k):
            chunk = hit.chunk
            out(
                f"{hit.score:.4f}  {chunk.path}:{chunk.start_line}-{chunk.end_line}  "
                f"{chunk.symbol or '-'}  ({', '.join(hit.reasons)})"
            )
    else:
        cases = cases_from_git_log(repository, limit=args.limit)
        report = evaluate_retrieval(index, cases, k=args.k)
        for case in report.cases:
            out(f"{'hit ' if case.hit else 'miss'}  {case.name}  expected={case.expected}")
        out(f"recall@{args.k} = {report.recall_at_k:.2f} over {len(report.cases)} cases")
    return 0


def cmd_run(args: argparse.Namespace, out: Printer) -> int:
    settings = _settings()
    service = make_service(settings, args.repository)
    config = _config(settings, args.config, args.tool_mode, not args.no_index)
    issue = Issue(
        repository=args.remote or args.repository.resolve().name,
        number=args.issue,
        title=args.title,
        body=args.body,
    )
    run, state = service.create_run(issue, config=config, remote=args.remote)
    out(f"run {run.id} created")
    if args.graph:
        final, pending = service.run_graph(state)
        if pending is not None:
            out(
                f"paused at the {pending['kind']} gate; resume with: "
                f"ase resume {args.repository} {run.id} --approve|--reject"
            )
            return 0
        _print_state(final, out)
        return 0
    gate: ApprovalGate = AutoApprove() if args.auto_approve else _terminal_gate(out)
    final = service.run_sequential(state, gate)
    _print_state(final, out)
    return 0 if final.get("outcome") == "submitted" else 1


def cmd_resume(args: argparse.Namespace, out: Printer) -> int:
    service = make_service(_settings(), args.repository)
    decision = {"approved": bool(args.approve), "reason": args.reason}
    final, pending = service.resume_graph(args.run_id, decision)
    if pending is not None:
        out(f"paused at the {pending['kind']} gate; resume again to continue")
        return 0
    _print_state(final, out)
    return 0


def cmd_eval(args: argparse.Namespace, out: Printer) -> int:
    from ase.evals import (
        EvalRunner,
        GitHarvester,
        Grader,
        load_suite,
        read_results,
        render_cases,
        render_table,
        summarize,
    )

    settings = _settings()
    if args.eval_command == "harvest":
        harvester = GitHarvester(
            WorkspaceManager(settings.workspaces_dir),
            lambda root: build_sandbox(root, settings.sandbox_backend, settings.docker_image),
            sys.executable if settings.sandbox_backend == "local" else None,
        )
        suite = harvester.harvest(args.repository.resolve(), limit=args.limit)
        suite.write(args.output)
        out(f"wrote {len(suite.tasks)} tasks to {args.output}")
        return 0
    if args.eval_command == "run":
        service = make_service(settings, args.repository)
        grader = Grader(
            WorkspaceManager(settings.workspaces_dir),
            lambda root: build_sandbox(root, settings.sandbox_backend, settings.docker_image),
            sys.executable if settings.sandbox_backend == "local" else None,
        )
        config = _config(settings, args.config, args.tool_mode, not args.no_index)
        results = EvalRunner(service, grader, args.results_dir).run_suite(
            load_suite(args.suite), config
        )
        out(render_table(summarize(results)))
        return 0
    results = read_results(args.results_dir)
    out(render_table(summarize(results)))
    if args.cases:
        out(render_cases(results))
    return 0


def cmd_mutate(args: argparse.Namespace, out: Printer) -> int:
    from ase.testing import MutationRunner, survivors_to_tasks

    settings = _settings()
    root = args.repository.resolve()
    sandbox = build_sandbox(root, settings.sandbox_backend, settings.docker_image)
    python = sys.executable if settings.sandbox_backend == "local" else None
    result = MutationRunner(sandbox, python).run(args.paths, args.tests, budget_s=args.budget)
    out(
        f"mutants: {len(result.mutants)}  killed: {result.killed}  survived: {result.survived}  "
        f"timeouts: {result.timeouts}  score: {result.score}  elapsed: {result.elapsed_s}s"
    )
    for task in survivors_to_tasks(result, root.name):
        out(f"- {task.issue.title}")
    return 0


def cmd_gen_tests(args: argparse.Namespace, out: Printer) -> int:
    from ase.testing import CoverageReport, CoverageRunner, TestGenerator

    settings = _settings()
    service = make_service(settings, args.repository)
    root = args.repository.resolve()
    sandbox = build_sandbox(root, settings.sandbox_backend, settings.docker_image)
    python = sys.executable if settings.sandbox_backend == "local" else None
    report = CoverageRunner(sandbox, python).run(args.sources or args.paths)
    if report is None:
        report = CoverageReport(percent=0.0)
        out("coverage.py is not available in the sandbox; generating without coverage hints")
    generator = TestGenerator(service.llm, settings.coder_model, sandbox, python)
    result = generator.generate(report, args.paths)
    for test in result.tests:
        out(f"{test.status:<9} {test.path}  {test.reason}")
    return 0


def cmd_ci(args: argparse.Namespace, out: Printer) -> int:
    from ase.ci import CiWatcher
    from ase.github import GitHubApi

    settings = _settings()
    if not settings.github_token:
        out("GITHUB_TOKEN is required for CI triage")
        return 2
    service = make_service(settings, Path.cwd())
    run = service.store.get(args.run_id)
    if run is None:
        out(f"unknown run {args.run_id}")
        return 2
    watcher = CiWatcher(GitHubApi(settings.github_token, settings.github_api_url), service.store)
    outcome = watcher.check(run)
    out(outcome.model_dump_json(indent=2))
    return 0


def cmd_reviews(args: argparse.Namespace, out: Printer) -> int:
    from ase.feedback import ReviewSync
    from ase.github import GitHubApi

    settings = _settings()
    if not settings.github_token:
        out("GITHUB_TOKEN is required to sync reviews")
        return 2
    service = make_service(settings, Path.cwd())
    sync = ReviewSync(GitHubApi(settings.github_token, settings.github_api_url), service.store)
    added = sync.sync_all(service.store.list())
    out(f"mirrored {added} new review events")
    return 0


def cmd_feedback(args: argparse.Namespace, out: Printer) -> int:
    from ase.feedback import (
        HeuristicScorer,
        LogisticScorer,
        NotEnoughLabels,
        agreement,
        build_dataset,
        export_dataset,
        load_dataset,
        train_logistic,
    )

    settings = _settings()
    if args.feedback_command == "dataset":
        service = make_service(settings, Path.cwd())
        dataset = build_dataset(service.store, service.store.list())
        count = export_dataset(dataset, args.output)
        out(f"exported {count} labelled trajectories to {args.output}")
        return 0
    dataset = load_dataset(args.dataset)
    if args.feedback_command == "train":
        try:
            scorer = train_logistic([(item.features, item.label) for item in dataset])
        except NotEnoughLabels as exc:
            out(f"not trained: {exc}")
            return 1
        scorer.save(args.output)
        out(f"saved logistic scorer to {args.output}")
        return 0
    chosen = LogisticScorer.load(args.model) if args.model else HeuristicScorer()
    report = agreement(chosen, dataset)
    out(report.model_dump_json(indent=2, exclude={"scores"}))
    return 0


def cmd_canary(args: argparse.Namespace, out: Printer) -> int:
    from ase.ci import CanaryController, Window
    from ase.policy import Policy, PolicyEngine

    windows = [Window.model_validate(item) for item in json.loads(args.windows.read_text())]
    controller = CanaryController(PolicyEngine(Policy.for_repository(Path.cwd())))
    out(controller.simulate(args.image, windows).model_dump_json(indent=2))
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace, Printer], int]] = {
    "index": cmd_index,
    "analyze": cmd_analyze,
    "knowledge": cmd_knowledge,
    "run": cmd_run,
    "resume": cmd_resume,
    "eval": cmd_eval,
    "mutate": cmd_mutate,
    "gen-tests": cmd_gen_tests,
    "ci": cmd_ci,
    "reviews": cmd_reviews,
    "feedback": cmd_feedback,
    "canary": cmd_canary,
}


def main(argv: list[str] | None = None, out: Printer = print) -> int:
    args = parser().parse_args(argv)
    return HANDLERS[args.command](args, out)


if __name__ == "__main__":
    sys.exit(main())
