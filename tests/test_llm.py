import json

import httpx
import pytest

from ase.llm import (
    LLMError,
    Message,
    ScriptedClient,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    Usage,
    build_client,
    estimate_cost,
    price_for,
    text_completion,
    tool_completion,
)
from ase.llm.anthropic import AnthropicClient


def test_pricing_table_and_fallback() -> None:
    assert price_for("claude-sonnet-5").input_per_million == 2.0
    assert price_for("claude-haiku-4-5-20251001").output_per_million == 5.0
    assert price_for("mystery-model") == price_for("nope")  # fallback is the priciest tier
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    assert estimate_cost("claude-sonnet-5", usage) == pytest.approx(2.0 + 1.0 + 0.2)


def test_scripted_client_replays_and_records() -> None:
    client = ScriptedClient(
        [text_completion("hello"), lambda request: tool_completion("read_file", {"path": "x"})]
    )
    first = client.complete(model="m", system="s", messages=[Message.user("hi")])
    assert first.text == "hello" and first.model == "m" and not first.tool_uses
    second = client.complete(model="m", system="s", messages=[Message.user("go")])
    assert second.tool_uses[0].name == "read_file" and second.stop_reason == "tool_use"
    assert client.requests[1].last_text == "go"
    with pytest.raises(LLMError):
        client.complete(model="m", system="s", messages=[])
    assert isinstance(build_client(None), ScriptedClient)


def test_anthropic_client_speaks_the_messages_api() -> None:
    seen: list[dict[str, object]] = []
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        assert request.headers["x-api-key"] == "key" and request.url.path == "/v1/messages"
        payload = json.loads(request.content)
        seen.append(payload)
        if attempts["count"] == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "Let me look."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "a.py"},
                    },
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 2,
                },
            },
        )

    slept: list[float] = []
    client = AnthropicClient(
        "key",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test"),
        sleep=slept.append,
    )
    tools = [ToolSpec(name="read_file", description="d", input_schema={"type": "object"})]
    messages = [
        Message.user("fix it"),
        Message.assistant([TextBlock(text="ok")]),
        Message.tool_results([ToolResultBlock(tool_use_id="t", content="x", is_error=True)]),
    ]
    completion = client.complete(
        model="claude-sonnet-5", system="sys", messages=messages, tools=tools
    )
    assert completion.text == "Let me look." and completion.tool_uses[0].input == {"path": "a.py"}
    assert completion.usage.cache_read_tokens == 3 and completion.usage.cache_write_tokens == 2
    assert slept == [2.0]
    payload = seen[-1]
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}  # type: ignore[index]
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}  # type: ignore[index]
    serialized = payload["messages"]
    assert serialized[2]["content"][0]["type"] == "tool_result"  # type: ignore[index]
    assert serialized[1]["content"][0]["text"] == "ok"  # type: ignore[index]


def test_anthropic_client_raises_on_hard_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    client = AnthropicClient(
        "key", client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x")
    )
    with pytest.raises(LLMError):
        client.complete(model="m", system="s", messages=[Message.user("hi")])
