"""Structured human feedback ledger."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class FeedbackDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class ReviewFeedback(BaseModel):
    run_id: str
    stage: str
    decision: FeedbackDecision
    reason_codes: list[str] = Field(default_factory=list)
    comment: str = ""
    accepted_files: list[str] = Field(default_factory=list)
    rejected_files: list[str] = Field(default_factory=list)


class FeedbackStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS review_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                decision TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL)"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def append(self, feedback: ReviewFeedback) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO review_feedback(run_id, stage, decision, payload, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    feedback.run_id,
                    feedback.stage,
                    feedback.decision.value,
                    feedback.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("feedback insert did not return an ID")
            return cursor.lastrowid

    def for_run(self, run_id: str) -> list[ReviewFeedback]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM review_feedback WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        return [ReviewFeedback.model_validate_json(row["payload"]) for row in rows]
