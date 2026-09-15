"""Environment-backed configuration with fail-closed production defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    repository_root: Path
    database_path: Path
    model_endpoint: str | None
    model_name: str
    model_api_key: str | None
    github_token: str | None
    github_webhook_secret: str | None
    execution_backend: str
    network_enabled: bool

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            repository_root=Path(os.environ.get("ASE_REPOSITORY_ROOT", Path.cwd())).resolve(),
            database_path=Path(os.environ.get("ASE_DATABASE_PATH", ".ase/ase.db")).resolve(),
            model_endpoint=os.environ.get("ASE_MODEL_ENDPOINT"),
            model_name=os.environ.get("ASE_MODEL_NAME", "model-not-configured"),
            model_api_key=os.environ.get("ASE_MODEL_API_KEY"),
            github_token=os.environ.get("GITHUB_TOKEN"),
            github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET"),
            execution_backend=os.environ.get("ASE_EXECUTION_BACKEND", "local"),
            network_enabled=_bool("ASE_SANDBOX_NETWORK"),
        )

    def validate_production(self) -> list[str]:
        errors: list[str] = []
        if self.execution_backend == "local":
            errors.append("production requires an isolated execution backend")
        if self.network_enabled:
            errors.append("sandbox network must be denied by default")
        if not self.github_webhook_secret:
            errors.append("GITHUB_WEBHOOK_SECRET is required")
        return errors
