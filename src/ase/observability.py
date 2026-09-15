"""OpenTelemetry initialization and secret-aware structured logging."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

SECRET_PATTERN = re.compile(r"(?i)(authorization|api[_-]?key|token|secret)(\s*[:=]\s*)([^\s,;]+)")


def redact(value: str) -> str:
    return SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        return json.dumps(payload, separators=(",", ":"))


def configure_telemetry(service_name: str = "ase-control-plane") -> trace.Tracer:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
