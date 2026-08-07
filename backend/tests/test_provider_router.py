"""Der Provider-Router: Cooldown/Fallback UND die max_tokens-Eskalation.

Anlass: der KI-&-Tech-News-Job riss ab dem 03.08. jeden Tag an Anthropics
`stop_reason=max_tokens`-Abbruch (Prompt fordert „KEINE Längenbegrenzung" — ein langer
Recherche-Digest sprengt das Standardbudget). Der Router retryte diesen Fehler zwar
(`retryable=True`), aber MIT UNVERÄNDERTEM `max_tokens` — der zweite Versuch scheiterte
also exakt gleich, und nach `_MAX_ATTEMPTS` gab der Router auf. Jetzt verdoppelt er das
Budget vor jedem weiteren Versuch, bis zu einem Deckel.
"""
import pytest
from app.worker.providers.base import ChatResponse, ProviderError
from app.worker.providers.router import Router


class _FakeImpl:
    """Ersetzt `_impl` — reicht `chat` an ein Skript von Antworten/Ausnahmen durch."""

    def __init__(self, skript):
        self._rest = list(skript)
        self.calls: list[int] = []

    async def chat(self, *, model, messages, tools=None, temperature=0.3, max_tokens=4096,
                   web_search=False, auth_token=None, **kw):
        self.calls.append(max_tokens)
        naechste = self._rest.pop(0) if self._rest else ChatResponse(text="fertig")
        if isinstance(naechste, Exception):
            raise naechste
        return naechste


@pytest.fixture
def router():
    return Router()


async def test_max_tokens_abbruch_wird_mit_hoeherem_budget_wiederholt(router, monkeypatch):
    impl = _FakeImpl([
        ProviderError("claude: Antwort bei max_tokens abgeschnitten", retryable=True,
                     escalate_max_tokens=True),
        ChatResponse(text="fertig"),
    ])
    monkeypatch.setattr(router, "_impl", lambda prov, base_url=None: impl)
    resp = await router.chat(provider="claude_code", model="sonnet", messages=[],
                             max_tokens=4096)
    assert resp.text == "fertig"
    assert impl.calls == [4096, 8192]      # zweiter Versuch mit verdoppeltem Budget


async def test_max_tokens_eskalation_hat_einen_deckel(router, monkeypatch):
    fehler = ProviderError("claude: Antwort bei max_tokens abgeschnitten", retryable=True,
                           escalate_max_tokens=True)
    impl = _FakeImpl([fehler, fehler, fehler, fehler, fehler])
    monkeypatch.setattr(router, "_impl", lambda prov, base_url=None: impl)
    with pytest.raises(ProviderError):
        await router.chat(provider="claude_code", model="sonnet", messages=[], max_tokens=4096)
    # Budget wächst, überschreitet aber niemals den Deckel — kein unbegrenztes Aufblähen.
    assert impl.calls == sorted(impl.calls)
    assert max(impl.calls) <= 64000


async def test_normaler_verbindungsfehler_eskaliert_max_tokens_nicht(router, monkeypatch):
    """Ohne `escalate_max_tokens` bleibt das bisherige Verhalten unverändert: ein
    Verbindungsfehler (status=None) bricht nach dem einen Versuch ab (kein 429/529-Backoff-
    Pfad) — insbesondere wird `max_tokens` dabei NICHT verdoppelt."""
    impl = _FakeImpl([ProviderError("claude: Verbindungsfehler", retryable=True)])
    monkeypatch.setattr(router, "_impl", lambda prov, base_url=None: impl)
    with pytest.raises(ProviderError):
        await router.chat(provider="claude_code", model="sonnet", messages=[], max_tokens=4096)
    assert impl.calls == [4096]
