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
from app.services import mail_intake
from app.services.workflow_seed import ensure_builtin_set
from sqlalchemy import select

from conftest import make_user


@pytest.fixture
async def anna(db):
    await ensure_builtin_set(db)
    return await make_user(db, "anna")


async def _mail(db, owner_id, agent: str, uid: int) -> AssistantTask:
    ids = await mail_intake.intake_mail(
        db, owner_id,
        {"account": "privat", "uid": uid, "from": "rechnung@beispiel.de",
         "subject": "Rechnung 4711", "body": "IBAN DE12 3456 7890, 129,90 EUR"},
        source="mail", agent=agent)
    assert ids, "der ausgelieferte Mail-Eingang hört nicht auf mail.received"
    return (await db.execute(select(AssistantTask).where(
        AssistantTask.source_ref == f"privat:{uid}"))).scalars().one()


async def test_lokaler_agent_bekommt_den_volltext(db, anna):
    db.add(ProviderToken(user_id=anna.id, provider="openai", name="local",
                         value_enc=encrypt_secret("k"), base_url="http://litellm/v1",
                         is_default=True))
    db.add(AgentDefinition(role="hausmeister", user_id=anna.id, provider="openai",
                           model="qwen3.6-35b-q8", token_name="local", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna.id, "hausmeister", 1)
    assert task.redaction == "unredacted"
    assert "IBAN DE12 3456 7890" in (task.raw_body or "")


async def test_agent_beim_anbieter_bleibt_geschwaerzt(db, anna):
    """Claude runs outside, and there the redaction is exactly the protection it is about."""
    db.add(AgentDefinition(role="assistent", user_id=anna.id, provider="claude_code",
                           model="claude-opus-5", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna.id, "assistent", 2)
    assert task.redaction == "redacted"
    assert task.raw_body is None


async def test_openai_ohne_eigenen_endpoint_bleibt_geschwaerzt(db, anna):
    """`openai` without a base URL is the real OpenAI, and that is outside as well."""
    db.add(ProviderToken(user_id=anna.id, provider="openai", name="cloud",
                         value_enc=encrypt_secret("sk-x"), base_url=None, is_default=True))
    db.add(AgentDefinition(role="wolke", user_id=anna.id, provider="openai",
                           model="gpt-4o", token_name="cloud", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna.id, "wolke", 3)
    assert task.redaction == "redacted"
    assert task.raw_body is None


async def test_dieselbe_mail_zweimal_bleibt_ein_item(db, anna):
    """The watcher likes to deliver twice on restarts, and that must double nothing."""
    await _mail(db, anna.id, "assistent", 4)
    ids = await mail_intake.intake_mail(
        db, anna.id, {"account": "privat", "uid": 4, "from": "rechnung@beispiel.de",
                      "subject": "Rechnung 4711", "body": "egal"},
        source="mail", agent="assistent")
    assert ids == []
    items = (await db.execute(select(AssistantTask).where(
        AssistantTask.source_ref == "privat:4"))).scalars().all()
    assert len(items) == 1
