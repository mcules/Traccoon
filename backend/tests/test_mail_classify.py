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


async def test_thinking_is_switched_off(db, anna, monkeypatch):
    """Without switching it off, empty text comes back, and nobody notices."""
    seen = {}

    async def fake_chat(self, **kw):
        seen.update(kw)
        return ChatResponse(text='{"category": "rechnung", "priority": "normal", '
                                 '"sensitive": true, "redacted_summary": "Eine Rechnung liegt vor."}')

    monkeypatch.setattr(mail_classify.OpenAIProvider, "chat", fake_chat)
    out = await mail_classify.classify_email(
        db, anna.id, account="privat", sender="rechnung@beispiel.de",
        subject="Rechnung 4711", body="IBAN DE12 3456", classify_agent="mail_classifier")

    assert seen["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert out["category"] == "rechnung"
    assert out["redacted_summary"] == "Eine Rechnung liegt vor."


async def test_an_empty_answer_falls_back_safely(db, anna, monkeypatch):
    """The emergency default must give nothing outside: sensitive, without a summary."""
    async def fake_chat(self, **kw):
        return ChatResponse(text="")

    monkeypatch.setattr(mail_classify.OpenAIProvider, "chat", fake_chat)
    out = await mail_classify.classify_email(
        db, anna.id, account="privat", sender="x@y.z", subject="s", body="geheim",
        classify_agent="mail_classifier")
    assert out["sensitive"] is True and out["redacted_summary"] == ""
    assert "geheim" not in str(out)


async def test_recipient_and_link_targets_reach_the_model(db, anna, monkeypatch):
    """The two facts the text does not carry, and the ones a phish trips over: whom it was
    sent to, and where its buttons lead."""
    seen = {}

    async def fake_chat(self, **kw):
        seen.update(kw)
        return ChatResponse(text='{"category": "phishing", "spam_score": 0.97, "betrug": true}')

    monkeypatch.setattr(mail_classify.OpenAIProvider, "chat", fake_chat)
    out = await mail_classify.classify_email(
        db, anna.id, account="privat", sender="Finom Support <info@dachdecker.example>",
        subject="Geräteautorisierung", body="Bitte verifizieren Sie Ihr Gerät.",
        classify_agent="mail_classifier", recipient="de@catchall.example",
        link_hosts=["verify-finom.example"])

    prompt = seen["messages"][1]["content"]
    assert "An: de@catchall.example" in prompt
    assert "Linkziele (Hosts): verify-finom.example" in prompt
    assert out["betrug"] is True and out["spam_score"] == 0.97


def test_link_hosts_come_out_once_each_and_in_order():
    from app.services.mail_actions import _link_hosts
    payload = {"links": [{"href": "https://Shop.example/a", "text": "a"},
                         {"href": "https://shop.example/b", "text": "b"},
                         {"href": "http://tracker.example/x", "text": "c"},
                         {"href": "mailto:x@y.z", "text": "d"}, "kaputt", None]}
    assert _link_hosts(payload) == ["shop.example", "tracker.example"]
    assert _link_hosts({}) == []


# --- What the model returns ------------------------------------------------------------

def test_the_string_false_is_not_a_yes():
    """`bool("false")` is True. Small models answer with exactly that string, and without
    this helper every one of them would carry a mail over the auto threshold."""
    from app.services.mail_classify import yes

    assert yes(True) is True and yes("true") is True and yes("Ja") is True and yes(1) is True
    assert yes("false") is False and yes("nein") is False and yes("0") is False
    assert yes(None) is False and yes("") is False and yes(0) is False


def test_features_are_normalised_and_capped():
    """The key is counted later, so an invented spelling would become a category of its own."""
    from app.services.mail_classify import features

    aus = features([
        {"kennung": "Marke Fremde-Domain!", "text": "  gibt sich als Bank aus  "},
        {"kennung": "marke_fremde_domain", "text": "doppelt"},
        {"kennung": "", "text": "ohne Kennung"},
        "kein Objekt",
        *[{"kennung": f"weiteres_{i}", "text": "x"} for i in range(6)],
    ])
    assert aus[0] == {"kennung": "marke_fremde_domain", "text": "gibt sich als Bank aus"}
    assert len(aus) == 5, "five at most, otherwise the model lists instead of judging"
    assert all(k["kennung"] for k in aus)


def test_features_without_a_list_stay_empty():
    from app.services.mail_classify import features

    assert features(None) == [] and features("phishing") == [] and features({}) == []
