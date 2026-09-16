# ADR 0008: the model client speaks the Messages API over httpx, not an SDK

**Status:** accepted

## Context

This is a learning project as much as a platform. The interesting parts of an agent loop
are the wire shapes: tool definitions, `tool_use` and `tool_result` blocks, cache-control
markers, usage accounting. An SDK hides exactly those.

## Decision

`ase.llm.anthropic.AnthropicClient` builds Messages API requests by hand over `httpx`,
with retries on 429 and 5xx, prompt-cache markers on the system prompt and tool
definitions, and usage parsed into `Usage` (input, output, cache read, cache write).
`ase.llm.pricing` turns usage into dollars for the budget. `ScriptedClient` implements
the same `ModelClient` protocol from canned completions so the whole workflow is tested
offline. Model ids and prices are configuration, not code.

## Consequences

- Every request and response shape is visible in one file; a new provider is one more
  implementation of `ModelClient`.
- We track API changes ourselves instead of upgrading an SDK.
- No streaming; the agent loop does not need it.
