from pathlib import Path

from ase.config import Settings


def test_production_validation_fails_closed() -> None:
    settings = Settings(
        repository_root=Path("/tmp"),
        database_path=Path("/tmp/db"),
        model_endpoint=None,
        model_name="none",
        model_api_key=None,
        github_token=None,
        github_webhook_secret=None,
        execution_backend="local",
        network_enabled=True,
    )
    assert len(settings.validate_production()) == 3
