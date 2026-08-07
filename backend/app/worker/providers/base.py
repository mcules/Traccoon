"""Einheitliches Provider-Interface (Port aus dem Vorläufer).

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
    # Gecachte (per Prompt-Caching ~0,1x berechnete) Input-Tokens dieses Calls.
    # Default 0 → Provider ohne Caching (codex/openai) bleiben unberührt.
    cache_read_tokens: int = 0
    # Wer tatsächlich geantwortet hat. Bei einem Fallback ist das NICHT der am Agenten
    # eingestellte Provider/Modell — und genau damit wurde der ganze Lauf bisher bepreist.
    # Die Adapter füllen das nicht, der Router setzt es: Default leer, damit sich für
    # niemanden sonst etwas ändert.
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
        ...
