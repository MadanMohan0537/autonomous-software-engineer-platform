"""Model clients: a provider-neutral protocol, the Anthropic wire client, a scripted fake."""

from ase.llm.client import (
    Completion,
    ContentBlock,
    LLMClient,
    LLMError,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from ase.llm.pricing import estimate_cost, price_for
from ase.llm.scripted import ScriptedClient, text_completion, tool_completion

__all__ = [
    "Completion",
    "ContentBlock",
    "LLMClient",
    "LLMError",
    "Message",
    "ScriptedClient",
    "TextBlock",
    "ToolResultBlock",
    "ToolSpec",
    "ToolUseBlock",
    "Usage",
    "estimate_cost",
    "price_for",
    "text_completion",
    "tool_completion",
]


def build_client(api_key: str | None, base_url: str = "https://api.anthropic.com") -> LLMClient:
    """The Anthropic client when a key is configured; otherwise an empty scripted client."""
    if api_key:
        from ase.llm.anthropic import AnthropicClient

        return AnthropicClient(api_key, base_url=base_url)
    return ScriptedClient()
