"""Echter OpenAI-API-Provider (Chat Completions, sk-API-Key).

Additiv neben claude_code (Anthropic-OAuth) und codex (ChatGPT-Subscription).
Da der interne Message-/Tool-Kontrakt bereits OpenAI-Format ist, geht das Body
weitgehend direkt an die API. `auth_token` = sk-… API-Key.
"""
from __future__ import annotations

from typing import Any

import httpx

from .base import ChatResponse, Provider, ProviderError, ToolCall


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, model: str = "gpt-4o", base_url: str = "https://api.openai.com/v1",
                 timeout: float = 300.0):
        self.model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, token: str | None) -> dict[str, str]:
        if not token:
            raise ProviderError("openai: kein API-Key (Secret-Tresor leer).")
        return {"Authorization": f"Bearer {token}", "content-type": "application/json"}

    async def chat(self, *, model: str, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None,
                   temperature: float = 0.3, max_tokens: int = 4096,
                   web_search: bool = False, auth_token: str | None = None) -> ChatResponse:
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,               # bereits OpenAI-Format
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools               # bereits {type:function, function:{…}}
            body["tool_choice"] = "auto"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/chat/completions",
                                         headers=self._headers(auth_token), json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai: Verbindungsfehler: {exc}", retryable=True) from exc

        if resp.status_code != 200:
            retryable = resp.status_code in (429, 500, 502, 503, 504)
            retry_after = None
            if resp.status_code == 429:
                hv = resp.headers.get("retry-after")
                try:
                    retry_after = float(hv) if hv else None
                except ValueError:
                    retry_after = None
            raise ProviderError(
                f"openai: HTTP {resp.status_code}: {resp.text[:400]}",
                status=resp.status_code, retryable=retryable, retry_after=retry_after)
        return self._parse(resp.json())

    def _parse(self, data: dict[str, Any]) -> ChatResponse:
        choices = data.get("choices") or [{}]
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args_raw = fn.get("arguments") or "{}"
            try:
                import json
                args = json.loads(args_raw)
            except Exception:  # noqa: BLE001
                args = {}
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
        # raw im erwarteten choices-Format (Runtime liest raw["choices"][0]["message"])
        return ChatResponse(text=text, tool_calls=calls,
                            raw={"choices": [{"message": msg}]}, usage=data.get("usage") or {})
