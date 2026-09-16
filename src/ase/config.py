"""Environment-backed configuration with fail-closed production defaults.

Secrets never travel further than the adapter that needs them: the model client gets the
model key, the delivery adapter gets the GitHub token, the sandbox gets neither. Two model
routes are configured here: the Anthropic Messages API used by the agent
(`ANTHROPIC_API_KEY`), and an OpenAI-compatible gateway used by the async planner and
test-proposal adapters (`ASE_MODEL_ENDPOINT`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ase.contracts import Budget, RunConfig, ToolMode


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    return env.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # repository, storage and execution
    repository_root: Path = field(default_factory=Path.cwd)
    database_path: Path = field(default_factory=lambda: Path(".ase/ase.db"))
    data_dir: Path = field(default_factory=lambda: Path(".ase"))
    execution_backend: str = "local"  # local | docker (alias: container)
    network_enabled: bool = False
    docker_image: str = "ase-sandbox:latest"
    # model routes
    model_endpoint: str | None = None
    model_name: str = "model-not-configured"
    model_api_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    planner_model: str = "claude-opus-5"
    coder_model: str = "claude-sonnet-5"
    classifier_model: str = "claude-haiku-4-5-20251001"
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-code-3"
    # GitHub
    github_token: str | None = None
    github_webhook_secret: str | None = None
    github_api_url: str = "https://api.github.com"
    # budgets
    budget: Budget = field(default_factory=Budget)

    @property
    def sandbox_backend(self) -> str:
        return "docker" if self.execution_backend in {"docker", "container"} else "local"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    def run_config(self, name: str = "default", tool_mode: ToolMode = "structured") -> RunConfig:
        return RunConfig(
            name=name,
            planner_model=self.planner_model,
            coder_model=self.coder_model,
            classifier_model=self.classifier_model,
            tool_mode=tool_mode,
            budget=self.budget,
        )

    def validate_production(self) -> list[str]:
        errors: list[str] = []
        if self.sandbox_backend == "local":
            errors.append("production requires an isolated execution backend")
        if self.network_enabled:
            errors.append("sandbox network must be denied by default")
        if not self.github_webhook_secret:
            errors.append("GITHUB_WEBHOOK_SECRET is required")
        return errors

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env: Mapping[str, str] = os.environ if environ is None else environ

        def text(name: str, default: str) -> str:
            return env.get(name, default) or default

        def optional(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        data_dir = Path(text("ASE_DATA_DIR", ".ase"))
        budget = Budget(
            max_iterations=int(text("ASE_MAX_ITERATIONS", "6")),
            max_tokens=int(text("ASE_MAX_TOKENS", "400000")),
            max_usd=float(text("ASE_MAX_USD", "3.0")),
            max_seconds=int(text("ASE_MAX_SECONDS", "900")),
        )
        backend = text("ASE_EXECUTION_BACKEND", text("ASE_SANDBOX", "local"))
        return cls(
            repository_root=Path(text("ASE_REPOSITORY_ROOT", str(Path.cwd()))).resolve(),
            database_path=Path(text("ASE_DATABASE_PATH", str(data_dir / "ase.db"))),
            data_dir=data_dir,
            execution_backend=backend,
            network_enabled=_bool(env, "ASE_SANDBOX_NETWORK"),
            docker_image=text("ASE_DOCKER_IMAGE", "ase-sandbox:latest"),
            model_endpoint=optional("ASE_MODEL_ENDPOINT"),
            model_name=text("ASE_MODEL_NAME", "model-not-configured"),
            model_api_key=optional("ASE_MODEL_API_KEY"),
            anthropic_api_key=optional("ANTHROPIC_API_KEY"),
            anthropic_base_url=text("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            planner_model=text("ASE_PLANNER_MODEL", "claude-opus-5"),
            coder_model=text("ASE_CODER_MODEL", "claude-sonnet-5"),
            classifier_model=text("ASE_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001"),
            voyage_api_key=optional("VOYAGE_API_KEY"),
            voyage_model=text("VOYAGE_MODEL", "voyage-code-3"),
            github_token=optional("GITHUB_TOKEN"),
            github_webhook_secret=optional("GITHUB_WEBHOOK_SECRET"),
            github_api_url=text("GITHUB_API_URL", "https://api.github.com"),
            budget=budget,
        )
