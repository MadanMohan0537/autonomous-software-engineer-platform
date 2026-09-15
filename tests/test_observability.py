import json
import logging

from ase.observability import JsonFormatter, redact


def test_redacts_secret_fields() -> None:
    assert "abc123" not in redact("Authorization: abc123")


def test_json_formatter() -> None:
    record = logging.LogRecord("ase", logging.INFO, "", 1, "token=secret", (), None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "token=[REDACTED]"
