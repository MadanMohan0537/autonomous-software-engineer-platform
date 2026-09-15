"""Provider-neutral message types and the `LLMClient` protocol.

The shapes mirror the Anthropic Messages API closely (text, tool_use and tool_result
blocks) because that is the wire format the platform speaks, but nothing outside this
package depends on the provider.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: list[ContentBlock]

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def assistant(cls, blocks: Sequence[TextBlock | ToolUseBlock]) -> Message:
        return cls(role="assistant", content=list(blocks))

    @classmethod
    def tool_results(cls, results: Sequence[ToolResultBlock]) -> Message:
        return cls(role="user", content=list(results))


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class Completion(BaseModel):
    model: str
    content: list[TextBlock | ToolUseBlock] = Field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: Usage = Field(default_factory=Usage)

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.content if isinstance(block, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [block for block in self.content if isinstance(block, ToolUseBlock)]


class LLMClient(Protocol):
    def complete(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion: ...


class LLMError(RuntimeError):
    pass
