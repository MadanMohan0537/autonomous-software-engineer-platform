"""A deterministic model for tests and zero-credential runs.

`ScriptedClient` replays completions in order, or calls a function that inspects the
request and decides what to answer. Every call is recorded so tests can assert on what
the agent asked the model.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ase.llm.client import (
    Completion,
    LLMError,
    Message,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)

Responder = Callable[["ScriptedRequest"], Completion]


class ScriptedRequest:
    def __init__(
        self, model: str, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> None:
        self.model = model
        self.system = system
        self.messages = list(messages)
        self.tools = list(tools)

    @property
    def last_text(self) -> str:
        for message in reversed(self.messages):
            for block in message.content:
                if isinstance(block, TextBlock):
                    return block.text
        return ""

    @property
    def last_tool_result(self) -> str:
        last = self.messages[-1] if self.messages else None
        if last is None:
            return ""
        return "\n".join(block.content for block in last.content if block.type == "tool_result")


def text_completion(text: str, model: str = "scripted", tokens: int = 100) -> Completion:
    return Completion(
        model=model,
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
        usage=Usage(input_tokens=tokens, output_tokens=tokens // 4),
    )


def tool_completion(
    name: str, arguments: dict[str, Any], text: str = "", model: str = "scripted"
) -> Completion:
    content: list[TextBlock | ToolUseBlock] = []
    if text:
        content.append(TextBlock(text=text))
    content.append(
        ToolUseBlock(
            id=f"toolu_{name}_{abs(hash(str(arguments))) % 10_000}", name=name, input=arguments
        )
    )
    return Completion(
        model=model,
        content=content,
        stop_reason="tool_use",
        usage=Usage(input_tokens=200, output_tokens=50),
    )


class ScriptedClient:
    def __init__(self, script: Sequence[Completion | Responder] = ()) -> None:
        self.script: list[Completion | Responder] = list(script)
        self.requests: list[ScriptedRequest] = []

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
        request = ScriptedRequest(model, system, messages, tools)
        self.requests.append(request)
        if not self.script:
            raise LLMError("scripted client has no completions left")
        item = self.script.pop(0)
        completion = item if isinstance(item, Completion) else item(request)
        return completion.model_copy(update={"model": model})
