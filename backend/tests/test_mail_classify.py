"""Die lokale Vorklassifizierung — der Schritt, der verhindert, dass Rohtext nach außen geht.

Anlass: Sie lief monatelang ins Leere. Das eingestellte Modell verbrauchte sein ganzes
Ausgabe-Budget im Reasoning und lieferte leeren Text; die Antwort war unparsbar und jede Mail
fiel auf den Notnagel zurück (sensitive=True, keine Zusammenfassung). Nach außen sah das nach
„nichts Auffälliges" aus — der Assistent bekam schlicht nie eine Zusammenfassung und musste
jede Mail selbst über IMAP lesen.
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
    """Ohne das Abschalten kommt leerer Text zurück — und niemand merkt es."""
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
    """Der Notnagel darf nichts nach außen geben: sensitive, ohne Zusammenfassung."""
    async def fake_chat(self, **kw):
        return ChatResponse(text="")

    monkeypatch.setattr(mail_classify.OpenAIProvider, "chat", fake_chat)
    out = await mail_classify.classify_email(
        db, anna.id, account="privat", sender="x@y.z", subject="s", body="geheim",
        classify_agent="mail_classifier")
    assert out["sensitive"] is True and out["redacted_summary"] == ""
    assert "geheim" not in str(out)
