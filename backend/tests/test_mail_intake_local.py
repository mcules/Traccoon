"""Redaction protects against the way outside, not against one's own machine.

If a model in one's own house processes the mail, no raw text leaves the server. The
redaction would then no longer be a protection but only a loss of information: the agent
would get a shortened summary and would have to fetch the same text back over the IMAP tools.

What is checked is the real way: report a mail, the shipped mail inbox runs, an assistant
item comes out. That makes the check hang off the flow that is in operation as well.
"""
import pytest
from app.core.security import encrypt_secret
from app.models.agents import AgentDefinition
from app.models.assistant import AssistantTask
from app.models.secrets import ProviderToken
from app.services import workflow_templates
from sqlalchemy import select

from conftest import make_user, make_webhook, report


@pytest.fixture
async def anna(db):
    user = await make_user(db, "anna")
    await workflow_templates.create(db, "mail-eingang", owner_id=user.id)
    await db.commit()
    return user


async def _mail(db, owner, agent: str, uid: int) -> AssistantTask:
    sub = await make_webhook(db, owner, "mail-test", mode="assistant", agent=agent,
                             classify_agent="mail_classifier")
    ids = await report(db, sub, {"account": "privat", "uid": uid,
                                "from": "rechnung@beispiel.de", "subject": "Rechnung 4711",
                                "body": "IBAN DE12 3456 7890, 129,90 EUR"})
    assert ids, "the shipped mail inbox does not listen for mail.received"
    return (await db.execute(select(AssistantTask).where(
        AssistantTask.source_ref == f"privat:{uid}"))).scalars().one()


async def test_a_local_agent_receives_the_full_text(db, anna):
    db.add(ProviderToken(user_id=anna.id, provider="openai", name="local",
                         value_enc=encrypt_secret("k"), base_url="http://litellm/v1",
                         is_default=True))
    db.add(AgentDefinition(role="hausmeister", user_id=anna.id, provider="openai",
                           model="qwen3.6-35b-q8", token_name="local", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna, "hausmeister", 1)
    assert task.redaction == "unredacted"
    assert "IBAN DE12 3456 7890" in (task.raw_body or "")


async def test_an_agent_at_a_provider_stays_redacted(db, anna):
    """Claude runs outside, and there the redaction is exactly the protection it is about."""
    db.add(AgentDefinition(role="assistent", user_id=anna.id, provider="claude_code",
                           model="claude-opus-5", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna, "assistent", 2)
    assert task.redaction == "redacted"
    assert task.raw_body is None


async def test_openai_without_an_own_endpoint_stays_redacted(db, anna):
    """`openai` without a base URL is the real OpenAI, and that is outside as well."""
    db.add(ProviderToken(user_id=anna.id, provider="openai", name="cloud",
                         value_enc=encrypt_secret("sk-x"), base_url=None, is_default=True))
    db.add(AgentDefinition(role="wolke", user_id=anna.id, provider="openai",
                           model="gpt-4o", token_name="cloud", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna, "wolke", 3)
    assert task.redaction == "redacted"
    assert task.raw_body is None


async def test_the_same_mail_twice_stays_one_item(db, anna):
    """The watcher likes to deliver twice on restarts, and that must double nothing.

    The key on the other hand stands on the trigger (`{account}:{uid}`) and no longer in the code
    of the mail intake — with that every webhook has it, not only this one.
    """
    sub = await make_webhook(db, anna, "mail-test", mode="assistant", agent="assistent")
    payload = {"account": "privat", "uid": 4, "from": "rechnung@beispiel.de",
                "subject": "Rechnung 4711", "body": "IBAN DE12 3456 7890, 129,90 EUR"}
    assert await report(db, sub, payload), "die erste Zustellung muss laufen"

    ids = await report(db, sub, {**payload, "body": "egal"})
    assert ids == []
    items = (await db.execute(select(AssistantTask).where(
        AssistantTask.source_ref == "privat:4"))).scalars().all()
    assert len(items) == 1
