"""Provider router with a cooldown and circuit breaker (ported from the predecessor, self-contained).

The provider map is lean: claude_code to Anthropic, codex to Codex. The token per provider
comes from the secret vault (resolved in the worker, passed through here).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .anthropic import AnthropicProvider
from .base import ChatResponse, Provider, ProviderError
from .codex import CodexProvider
from .openai import OpenAIProvider

log = logging.getLogger("traccoon.providers")

_COOLDOWN_DEFAULT = float(os.getenv("PROVIDER_COOLDOWN", "300"))
_COOLDOWN_MAX = 3600.0
_RATE_LIMIT_WAIT = float(os.getenv("PROVIDER_RATE_LIMIT_WAIT", "60"))
_RATE_LIMIT_WAIT_MAX = 180.0
_MAX_ATTEMPTS = int(os.getenv("PROVIDER_MAX_ATTEMPTS", "4"))

# Traccoon-Provider-Namen (AgentDefinition.provider) → Impl.
# claude_code = Anthropic-OAuth-Subscription, codex = ChatGPT-Subscription,
# openai = the real OpenAI API provider (sk key), standalone, NO longer a Codex alias.
_ANTHROPIC = {"claude_code", "claude", "anthropic"}
_CODEX = {"codex"}
_OPENAI = {"openai"}


class Router:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {
            "claude_code": AnthropicProvider(),
            "codex": CodexProvider(),
            "openai": OpenAIProvider(),
        }
        # OpenAI-compatible providers with a base URL of their own, cached by URL (the global
        # default OpenAIProvider stays untouched at api.openai.com).
        self._openai_by_url: dict[str, OpenAIProvider] = {}
        self._cooldown: dict[str, float] = {}

    def _openai_for(self, base_url: str) -> OpenAIProvider:
        impl = self._openai_by_url.get(base_url)
        if impl is None:
            impl = OpenAIProvider(base_url=base_url)
            self._openai_by_url[base_url] = impl
        return impl

    def _impl(self, provider: str, base_url: str | None = None) -> Provider | None:
        if provider in _ANTHROPIC:
            return self._providers["claude_code"]
        if provider in _CODEX:
            return self._providers["codex"]
        if provider in _OPENAI:
            # An own endpoint (a local litellm or similar) only for the OpenAI family.
            return self._openai_for(base_url) if base_url else self._providers["openai"]
        return None

    def _cooling(self, prov: str) -> bool:
        return self._cooldown.get(prov, 0.0) > time.monotonic()

    def _trip(self, prov: str, exc: ProviderError) -> None:
        cd = exc.retry_after if exc.retry_after else _COOLDOWN_DEFAULT
        cd = max(1.0, min(cd, _COOLDOWN_MAX))
        self._cooldown[prov] = time.monotonic() + cd
        log.warning("Provider '%s' rate limited (HTTP %s), cooldown %ss", prov, exc.status, int(cd))

    def cooldown_status(self) -> dict[str, int]:
        now = time.monotonic()
        return {p: int(d - now) for p, d in self._cooldown.items() if d > now}

    async def chat(self, *, provider: str, model: str, messages, tools=None,
                   temperature: float = 0.3, max_tokens: int = 4096,
                   fallback: str | None = None, fallback_model: str = "",
                   web_search: bool = False,
                   tokens: dict[str, str | None] | None = None,
                   base_urls: dict[str, str | None] | None = None,
                   extra_body: dict | None = None, effort: str = "") -> ChatResponse:
        tokens = tokens or {}
        base_urls = base_urls or {}
        raw_chain = [provider] + ([fallback] if fallback and fallback != provider else [])
        chain = [p for p in raw_chain if not self._cooling(p)] or raw_chain
        last_err: ProviderError | None = None
        for prov in chain:
            # The base URL strictly per provider; the legacy key fallback as with the token below.
            base_url = base_urls.get(prov)
            impl = self._impl(prov, base_url)
            if impl is None:
                last_err = ProviderError(f"Provider '{prov}' is not available")
                continue
            # Every provider has its own model names: primary to model, fallback to
            # fallback_model (empty means the provider default); the primary model is not carried over.
            use_model = model if prov == provider else fallback_model
            # The token strictly per provider; the legacy keys claude_code/codex only as a default fallback.
            token = tokens.get(prov)
            if token is None:
                legacy = "claude_code" if prov in _ANTHROPIC else "codex" if prov in _CODEX else prov
                token = tokens.get(legacy)
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    # `extra_body` is known only to the OpenAI-compatible provider
                    # (endpoint-owned fields like `chat_template_kwargs`). The subscription
                    # providers do NOT get it: there it would be an unknown field and a 400.
                    zusatz = {"extra_body": extra_body} if extra_body and prov in _OPENAI else {}
                    # Thinking depth is understood only by Anthropic (`output_config.effort`).
                    # On a fallback to codex or openai it drops out instead of causing a 400.
                    if effort and prov in _ANTHROPIC:
                        zusatz["effort"] = effort
                    resp = await impl.chat(model=use_model, messages=messages, tools=tools,
                                           temperature=temperature, max_tokens=max_tokens,
                                           web_search=web_search, auth_token=token, **zusatz)
                    # Only here is it settled WHO answered: after a fallback that is neither
                    # the configured provider nor its model. Without these lines the caller
                    # prices the whole run with the primary model.
                    resp.provider, resp.model = prov, use_model
                    return resp
                except ProviderError as exc:
                    last_err = exc
                    is_last = attempt >= _MAX_ATTEMPTS - 1
                    if exc.status == 529 and not is_last:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    if exc.status == 429 and not is_last:
                        wait = min(exc.retry_after or _RATE_LIMIT_WAIT, _RATE_LIMIT_WAIT_MAX)
                        self._trip(prov, exc)
                        await asyncio.sleep(wait)
                        continue
                    if exc.status in (429, 529):
                        self._trip(prov, exc)
                    elif not exc.retryable:
                        raise
                    break
        assert last_err is not None
        raise last_err


router = Router()
