"""Process-level settings read from the environment.

Secrets never travel further than the adapter that needs them: the model client gets the
Anthropic key, the delivery adapter gets the GitHub token, the sandbox gets neither.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ase.contracts import Budget, RunConfig, ToolMode

SandboxBackend = str  # "local" | "docker"


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-code-3"
    planner_model: str = "claude-opus-5"
    coder_model: str = "claude-sonnet-5"
    classifier_model: str = "claude-haiku-4-5-20251001"
    sandbox_backend: SandboxBackend = "local"
    docker_image: str = "ase-sandbox:latest"
    data_dir: Path = field(default_factory=lambda: Path(".ase"))
    budget: Budget = field(default_factory=Budget)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "runs.sqlite3"

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

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ

        def text(name: str, default: str) -> str:
            return env.get(name, default) or default

        def optional(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        budget = Budget(
            max_iterations=int(text("ASE_MAX_ITERATIONS", "6")),
            max_tokens=int(text("ASE_MAX_TOKENS", "400000")),
            max_usd=float(text("ASE_MAX_USD", "3.0")),
            max_seconds=int(text("ASE_MAX_SECONDS", "900")),
        )
        return cls(
            anthropic_api_key=optional("ANTHROPIC_API_KEY"),
            anthropic_base_url=text("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            github_token=optional("GITHUB_TOKEN"),
            github_api_url=text("GITHUB_API_URL", "https://api.github.com"),
            voyage_api_key=optional("VOYAGE_API_KEY"),
            voyage_model=text("VOYAGE_MODEL", "voyage-code-3"),
            planner_model=text("ASE_PLANNER_MODEL", "claude-opus-5"),
            coder_model=text("ASE_CODER_MODEL", "claude-sonnet-5"),
            classifier_model=text("ASE_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001"),
            sandbox_backend=text("ASE_SANDBOX", "local"),
            docker_image=text("ASE_DOCKER_IMAGE", "ase-sandbox:latest"),
            data_dir=Path(text("ASE_DATA_DIR", ".ase")),
            budget=budget,
        )
