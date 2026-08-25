"""Side tasks do not run on the model that does the actual work.

Summarising, titling, tidying up: that is diligence without judgement, and letting it run on
the same Sonnet or Opus that is thinking right now costs money and waiting time. The
predecessor had an `auxiliary:` block with a model of its own **per task**
(compression, title_generation, triage …); the default `auto` meant the main provider. That
is exactly what happens here, with Traccoon's building blocks: named provider tokens from the vault, router with a fallback.

Bewusste Eigenschaften:

* **Without configuration nothing changes.** No entry means `auto`, so the same provider and
  the same model as the calling agent. Whoever sets nothing notices nothing.
* **A side task must never tear the main run.** Every call is encapsulated; errors and
  timeouts deliver `None`, and the caller works on without the result (with the compaction
  that means: better a hard truncation than an abort).
* **No tools, no memory, no touching of the cache.** An aux call is a one-way conversation;
  it must not touch the carefully built prompt cache of the main run (Traccoon's main lever against token burn).
  anfassen (Traccoons Haupthebel gegen Token-Verbrennung).
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..services.appsettings import get_setting
from .providers.base import ProviderError
from .providers.router import router
from .secrets import resolve_provider_base_url, resolve_provider_token

log = logging.getLogger("traccoon.aux")

# The side tasks for which a setting of its own exists. Deliberately few: every further one
# wants a justification, because otherwise the configuration frays into settings nobody maintains.
AUX_TASKS = {
    "compression": "Summarise a long conversation (compacting the context)",
    "title": "Form a short title for an intake or a conversation",
    "curator": "Look through the learned memory and tidy it up",
}

# Key in app_settings, for instance `aux.compression`. The value is JSON:
#   {"provider": "openai", "model": "qwen3.6-35b-q3", "token_name": "aux", "timeout": 300}
# Empty or missing means `auto`.
def setting_key(task: str) -> str:
    return f"aux.{task}"


async def aux_config(db: AsyncSession, task: str) -> dict:
    """The configured model for a side task; an empty dict means `auto`."""
    raw = (await get_setting(db, setting_key(task), "")).strip()
    if not raw:
        return {}
    try:
        cfg = json.loads(raw)
    except ValueError:
        log.warning("aux.%s: the setting is not valid JSON, using the main provider", task)
        return {}
    return cfg if isinstance(cfg, dict) and cfg.get("provider") else {}


async def aux_chat(db: AsyncSession, *, owner_id: int | None, task: str, messages: list[dict],
                   agent=None, tokens: dict | None = None, base_urls: dict | None = None,
                   max_tokens: int = 2048, temperature: float = 0.2) -> str | None:
    """Give a side task to the model configured for it. `None` = it did not work.

    `agent`/`tokens`/`base_urls` are the context of the running agent; they carry the `auto`
    case (no model of its own configured) without an additional vault access.
    """
    cfg = await aux_config(db, task)
    if cfg:
        provider = str(cfg["provider"])
        model = str(cfg.get("model") or "")
        name = str(cfg.get("token_name") or "")
        token = await resolve_provider_token(db, owner_id, provider, name)
        base_url = cfg.get("base_url") or await resolve_provider_base_url(db, owner_id, provider, name)
        use_tokens = {provider: token}
        use_base_urls = {provider: base_url}
        timeout = float(cfg.get("timeout") or 120)
        # Endpoint-owned fields. Thinking models (qwen3.6 and company) otherwise use up their
        # whole output budget on reasoning and deliver empty text, and for diligence work like
        # summarising the thinking is wasted anyway. The default is therefore: off.
        extra = cfg.get("extra_body")
        if extra is None:
            extra = {"chat_template_kwargs": {"enable_thinking": False}}
    elif agent is not None:
        # `auto`: the same way as the main run, including its fallback chain.
        provider, model = agent.provider, agent.model
        use_tokens, use_base_urls = tokens or {}, base_urls or {}
        timeout = 120.0
        extra = None            # main provider: add nothing, the run knows its fields
    else:
        return None

    try:
        # Time cap: a side task must not hold the main run up. The predecessor regularly ran into 120 s
        # timeouts here because the compaction hung on the large model, which is why the
        # deadline is configurable and the failure has no consequences.
        resp = await asyncio.wait_for(
            router.chat(provider=provider, model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens,
                        tokens=use_tokens, base_urls=use_base_urls, extra_body=extra),
            timeout=timeout)
    except (ProviderError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        log.warning("aux.%s failed (%s), the caller continues without the result", task, exc)
        return None
    text = (resp.text or "").strip()
    return text or None
