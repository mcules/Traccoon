"""Uniform provider interface (ported from the predecessor).

Messages and tools in the OpenAI format (the internal lingua franca); providers that speak
differently (Anthropic, Codex) translate in their adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    # Gecachte (per Prompt-Caching ~0,1x berechnete) Input-Tokens dieses Calls.
    # Default 0: providers without caching (codex, openai) stay untouched.
    cache_read_tokens: int = 0
    # Who actually answered. On a fallback that is NOT the provider or model configured on
    # the agent, and with exactly that the whole run used to be priced. The adapters do not
    # fill it, the router sets it: empty by default, so that nothing changes for anybody else.
    provider: str = ""
    model: str = ""


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


class Provider(Protocol):
    name: str

    async def chat(self, *, model: str, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None,
                   temperature: float = 0.3, max_tokens: int = 4096,
                   web_search: bool = False, auth_token: str | None = None) -> ChatResponse:
        # `effort` (thinking depth) is known only to the Anthropic adapter; the router passes
        # it exclusively there, because elsewhere it would be an unknown field.
        ...
