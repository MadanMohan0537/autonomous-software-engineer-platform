"""Composition root for the agent: settings in, wired services out."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ase.agent.delivery import Delivery, DryRunDelivery, GitHubDelivery
from ase.agent.graph import GraphRunner, langgraph_available, sqlite_checkpointer
from ase.agent.nodes import AgentNodes, AgentServices
from ase.agent.runner import ApprovalGate, SequentialRunner
from ase.agent.state import AgentState, initial_state
from ase.config import Settings
from ase.contracts import AgentRun, Issue, RunConfig, Task, TaskSource
from ase.github import GitHubApi
from ase.llm import LLMClient, build_client
from ase.policy import Policy, PolicyEngine
from ase.repo_intelligence import KnowledgeIndex
from ase.repo_intelligence.embeddings import Embedder, select_embedder
from ase.sandbox import Sandbox, build_sandbox
from ase.store import PlatformStore, SqliteRunStore
from ase.workspace import WorkspaceManager


def load_or_build_index(
    repository: Path, sha: str, index_dir: Path, embedder: Embedder
) -> KnowledgeIndex:
    location = KnowledgeIndex.location(index_dir, sha)
    if (location / "meta.json").exists():
        return KnowledgeIndex.load(location, embedder)
    index = KnowledgeIndex.build(repository, sha, embedder=embedder, cache_dir=index_dir)
    index.save(location)
    return index


class AgentService:
    def __init__(
        self,
        settings: Settings,
        repository: Path,
        store: PlatformStore | None = None,
        llm: LLMClient | None = None,
        delivery: Delivery | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository.resolve()
        self.store: PlatformStore = store or SqliteRunStore(settings.database_path)
        self.llm: LLMClient = llm or build_client(
            settings.anthropic_api_key, settings.anthropic_base_url
        )
        self.policy = policy or PolicyEngine(Policy.for_repository(self.repository))
        self.embedder = select_embedder(settings.voyage_api_key, settings.voyage_model)
        if delivery is not None:
            self.delivery: Delivery = delivery
        elif settings.github_token:
            self.delivery = GitHubDelivery(
                GitHubApi(settings.github_token, base_url=settings.github_api_url)
            )
        else:
            self.delivery = DryRunDelivery(settings.data_dir / "patches")
        self._index_cache: dict[str, KnowledgeIndex] = {}

    # -- wiring ---------------------------------------------------------------------
    def _sandbox(self, root: Path) -> Sandbox:
        return build_sandbox(
            root,
            backend=self.settings.sandbox_backend,
            image=self.settings.docker_image,
            policy=self.policy,
        )

    def _index(self, repository: Path, sha: str) -> KnowledgeIndex:
        if sha not in self._index_cache:
            self._index_cache[sha] = load_or_build_index(
                repository, sha, self.settings.index_dir, self.embedder
            )
        return self._index_cache[sha]

    def services(self) -> AgentServices:
        return AgentServices(
            store=self.store,
            llm=self.llm,
            workspaces=WorkspaceManager(self.settings.workspaces_dir),
            policy=self.policy,
            sandbox_factory=self._sandbox,
            index_loader=self._index,
            delivery=self.delivery,
            python=None if self.settings.sandbox_backend == "docker" else sys.executable,
        )

    # -- runs -----------------------------------------------------------------------
    def create_run(
        self,
        issue: Issue,
        config: RunConfig | None = None,
        task: Task | None = None,
        remote: str | None = None,
    ) -> tuple[AgentRun, AgentState]:
        config = config or self.settings.run_config()
        task = task or Task(issue=issue, source=TaskSource.ISSUE)
        run = AgentRun(issue=issue, task=task, config=config)
        run.record("run_created", config=config.name, source=task.source.value)
        self.store.save(run)
        state = initial_state(run.id, str(self.repository), task, config, remote=remote)
        return run, state

    def run_sequential(self, state: AgentState, gate: ApprovalGate | None = None) -> AgentState:
        return SequentialRunner(AgentNodes(self.services()), gate).run(state)

    def graph_runner(self) -> GraphRunner:
        if not langgraph_available():
            raise RuntimeError("LangGraph is not installed; use run_sequential instead")
        checkpointer = sqlite_checkpointer(self.settings.data_dir / "checkpoints.sqlite3")
        return GraphRunner(AgentNodes(self.services()), checkpointer)

    def run_graph(self, state: AgentState) -> tuple[AgentState, dict[str, Any] | None]:
        """Start a durable run; returns the state and the pending interrupt, if any."""
        runner = self.graph_runner()
        result = runner.start(state, state["run_id"])
        return result, runner.pending(state["run_id"])

    def resume_graph(
        self, run_id: str, decision: dict[str, Any]
    ) -> tuple[AgentState, dict[str, Any] | None]:
        runner = self.graph_runner()
        result = runner.resume(run_id, decision)
        return result, runner.pending(run_id)
