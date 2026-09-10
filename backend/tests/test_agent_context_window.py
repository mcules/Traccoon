"""The compaction threshold belongs to the model, not to the agent definition.

Every definition in this house carried a hand set 180.000 while the models behind them hold
a million. That was not a safety net: run 2511 compacted at 173.736 tokens, spent 78 seconds
summarising, lost its prompt cache and read the same notes over again — for a limit the
provider had no objection to. Runs of this account have gone through at 655.415 tokens.
"""
from __future__ import annotations

import pytest

from app.models.agents import AgentDefinition
from app.models.ops import ProviderModel
from app.worker.__main__ import _load_agent


async def _agent(db, **kw) -> AgentDefinition:
    row = AgentDefinition(
        role="assistent", display_name="Assistent", system_prompt="tu was",
        provider="claude_code", model="claude-opus-5", effort="", temperature=0.0,
        max_tokens=16384, max_turns_planning=5, max_turns_execution=80,
        can_code=False, can_read_code=False, can_delegate=False, web_search=False,
        allowed_tools=[], allowed_skills=[], delegate_to=[], active=True, **kw)
    db.add(row)
    await db.commit()
    return row


@pytest.mark.asyncio
async def test_the_window_comes_from_the_model_catalogue(db) -> None:
    await _agent(db, max_context_tokens=None)
    db.add(ProviderModel(provider="claude_code", model="claude-opus-5",
                         display_name="Claude Opus 5", context_tokens=1_000_000))
    await db.commit()

    agent = await _load_agent(db, "assistent", 0, "execute")
    assert agent.max_context_tokens == 1_000_000


@pytest.mark.asyncio
async def test_a_value_on_the_agent_stays_an_override(db) -> None:
    """For whoever deliberately wants a tighter belt than the model."""
    await _agent(db, max_context_tokens=120_000)
    db.add(ProviderModel(provider="claude_code", model="claude-opus-5",
                         context_tokens=1_000_000))
    await db.commit()

    agent = await _load_agent(db, "assistent", 0, "execute")
    assert agent.max_context_tokens == 120_000


@pytest.mark.asyncio
async def test_a_model_nobody_catalogued_leaves_the_compaction_off(db) -> None:
    """As before: no number, no shortening. Better a provider error that names the cause
    than a threshold somebody guessed."""
    await _agent(db, max_context_tokens=None)
    agent = await _load_agent(db, "assistent", 0, "execute")
    assert agent.max_context_tokens is None
