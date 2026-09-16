"""GitHub webhook authentication and supported event decoding."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


class InvalidSignature(ValueError):
    pass


def verify_signature(body: bytes, signature: str | None, secret: str) -> None:
    if not signature or not signature.startswith("sha256="):
        raise InvalidSignature("missing GitHub signature")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignature("invalid GitHub signature")


@dataclass(frozen=True)
class IssueEvent:
    action: str
    repository: str
    number: int
    title: str
    body: str
    labels: list[str]


def decode_issue_event(payload: dict[str, Any]) -> IssueEvent:
    issue = payload["issue"]
    repository = payload["repository"]["full_name"]
    return IssueEvent(
        action=str(payload["action"]),
        repository=str(repository),
        number=int(issue["number"]),
        title=str(issue["title"]),
        body=str(issue.get("body") or ""),
        labels=[str(item["name"]) for item in issue.get("labels", [])],
    )
