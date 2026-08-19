"""The local pre-classification: the step that prevents raw text from going outside.

The occasion: it ran into nothing for months. The configured model used its whole output
budget on reasoning and delivered empty text; the answer was unparsable and every mail fell
back on the emergency default (sensitive=True, no summary). From the outside that looked
like "nothing conspicuous": the assistant simply never got a summary and had to read every
mail itself over IMAP.
"""
import pytest
from app.models.agents import AgentDefinition
from app.models.secrets import ProviderToken
from app.core.security import encrypt_secret
from app.services import mail_classify
from app.worker.providers.base import ChatResponse

from conftest import make_user


@pytest.fixture
async def anna(db):
    u = await make_user(db, "anna")
    db.add(ProviderToken(user_id=u.id, provider="openai", name="local",
                         value_enc=encrypt_secret("k"), base_url="http://litellm/v1",
                         is_default=True))
    db.add(AgentDefinition(role="mail_classifier", user_id=u.id, provider="openai",
                           model="qwen3.6-35b-q3", token_name="local", system_prompt=""))
    await db.commit()
    return u


async def test_denken_wird_abgeschaltet(db, anna, monkeypatch):
    """Without switching it off, empty text comes back, and nobody notices."""
    gesehen = {}

    async def fake_chat(self, **kw):
        gesehen.update(kw)
        return ChatResponse(text='{"category": "rechnung", "priority": "normal", '
                                 '"sensitive": true, "redacted_summary": "Eine Rechnung liegt vor."}')

    monkeypatch.setattr(mail_classify.OpenAIProvider, "chat", fake_chat)
    out = await mail_classify.classify_email(
        db, anna.id, account="privat", sender="rechnung@beispiel.de",
        subject="Rechnung 4711", body="IBAN DE12 3456", classify_agent="mail_classifier")

    assert gesehen["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert out["category"] == "rechnung"
    assert out["redacted_summary"] == "Eine Rechnung liegt vor."


async def test_leere_antwort_faellt_sicher_zurueck(db, anna, monkeypatch):
    """The emergency default must give nothing outside: sensitive, without a summary."""
    async def fake_chat(self, **kw):
        return ChatResponse(text="")

    monkeypatch.setattr(mail_classify.OpenAIProvider, "chat", fake_chat)
    out = await mail_classify.classify_email(
        db, anna.id, account="privat", sender="x@y.z", subject="s", body="geheim",
        classify_agent="mail_classifier")
    assert out["sensitive"] is True and out["redacted_summary"] == ""
    assert "geheim" not in str(out)
