"""Einheitliches Provider-Interface (Port aus nexus).

Messages/Tools im OpenAI-Format (interner Lingua-Franca); Provider, die anders
sprechen (Anthropic, Codex), übersetzen in ihrem Adapter.
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
        ...
