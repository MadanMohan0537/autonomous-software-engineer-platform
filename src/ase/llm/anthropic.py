"""Anthropic Messages API over plain HTTP.

Written against the wire protocol rather than an SDK so the request and response shapes
are visible: tool definitions, tool_use / tool_result blocks, prompt caching markers and
usage accounting are all right here. Retries with backoff on 429 and 5xx.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from ase.llm.client import (
    Completion,
    LLMError,
    Message,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)

API_VERSION = "2023-06-01"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _serialize_message(message: Message) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if block.type == "text":
            blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
        else:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role, "content": blocks}


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        client: httpx.Client | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client or httpx.Client(base_url=base_url, timeout=120)
        self.client.headers.update(
            {
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            }
        )
        self.max_retries = max_retries
        self._sleep = sleep

    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # The system prompt is the same for every turn of a run: cache it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [_serialize_message(message) for message in messages],
        }
        if tools:
            serialized = [tool.model_dump() for tool in tools]
            serialized[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = serialized

        attempt = 0
        while True:
            response = self.client.post("/v1/messages", json=payload)
            if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                attempt += 1
                self._sleep(min(2.0**attempt, 20.0))
                continue
            if response.status_code >= 400:
                raise LLMError(f"anthropic api error {response.status_code}: {response.text[:500]}")
            return self._parse(response.json())

    @staticmethod
    def _parse(data: dict[str, Any]) -> Completion:
        content: list[TextBlock | ToolUseBlock] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                content.append(TextBlock(text=str(block.get("text", ""))))
            elif block.get("type") == "tool_use":
                content.append(
                    ToolUseBlock(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        input=dict(block.get("input") or {}),
                    )
                )
        usage = data.get("usage") or {}
        return Completion(
            model=str(data.get("model", "")),
            content=content,
            stop_reason=str(data.get("stop_reason") or "end_turn"),
            usage=Usage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
            ),
        )
