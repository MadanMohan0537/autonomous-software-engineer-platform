"""Vendor-neutral model client with structured-response enforcement."""

from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ModelClient(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, endpoint: str, model: str, api_key: str | None = None) -> None:
        self.endpoint = endpoint
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def complete(self, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                self.endpoint,
                headers=self.headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])


async def structured_completion(
    client: ModelClient, schema: type[T], system: str, payload: dict[str, Any]
) -> T:
    instruction = (
        f"{system}\nReturn only JSON matching this schema: "
        f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
    )
    raw = await client.complete(instruction, json.dumps(payload, separators=(",", ":")))
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("model returned invalid JSON") from exc
    return schema.model_validate(value)
