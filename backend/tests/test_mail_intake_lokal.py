"""Schwärzen schützt vor dem Weg nach draußen — nicht vor dem eigenen Rechner.

Bearbeitet ein Modell im eigenen Haus die Mail, verlässt kein Rohtext den Server. Die
Schwärzung wäre dann kein Schutz mehr, sondern nur Informationsverlust: der Agent bekäme eine
verkürzte Zusammenfassung und müsste denselben Text über die IMAP-Tools wieder heranholen.
"""
import pytest
from app.core.security import encrypt_secret
from app.models.agents import AgentDefinition
from app.models.secrets import ProviderToken
from app.services import mail_intake

from conftest import make_user


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna")


async def _mail(db, owner_id, agent: str, uid: int):
    task, _auto = await mail_intake.intake_mail(
        db, owner_id,
        {"account": "privat", "uid": uid, "from": "rechnung@beispiel.de",
         "subject": "Rechnung 4711", "body": "IBAN DE12 3456 7890, 129,90 EUR"},
        source="mail", agent=agent)
    return task


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
    """Claude läuft auswärts — dort ist die Schwärzung genau der Schutz, um den es geht."""
    db.add(AgentDefinition(role="assistent", user_id=anna.id, provider="claude_code",
                           model="claude-opus-5", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna.id, "assistent", 2)
    assert task.redaction == "redacted"
    assert task.raw_body is None


async def test_openai_ohne_eigenen_endpoint_bleibt_geschwaerzt(db, anna):
    """`openai` ohne Base-URL ist das echte OpenAI — auch das ist auswärts."""
    db.add(ProviderToken(user_id=anna.id, provider="openai", name="cloud",
                         value_enc=encrypt_secret("sk-x"), base_url=None, is_default=True))
    db.add(AgentDefinition(role="wolke", user_id=anna.id, provider="openai",
                           model="gpt-4o", token_name="cloud", system_prompt=""))
    await db.commit()

    task = await _mail(db, anna.id, "wolke", 3)
    assert task.redaction == "redacted"
    assert task.raw_body is None
