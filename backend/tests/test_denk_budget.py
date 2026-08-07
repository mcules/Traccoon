"""Das Denken darf den Lauf nicht auffressen.

Auf sonnet-5/opus-5 ist adaptives Denken AN, sobald das Feld `thinking` fehlt — und es
teilt sich `max_tokens` mit der sichtbaren Antwort. Am 2026-08-07 starb daran erst der
Prüfer (Lauf 744, 41 Iterationen) und danach der Entwickler (Lauf 752, 29 Iterationen):
Budget im Denken verbraucht, Antwort abgeschnitten, Lauf verloren.

Zwei Schutzschichten werden hier festgenagelt:
1. `effort` am Agenten geht als `output_config.effort` mit — der saubere Hebel.
2. Läuft eine Antwort trotzdem in `max_tokens`, versucht der Provider es EINMAL ohne
   Denken. Erst wenn auch das abgeschnitten wird, stirbt der Zug.
"""
import pytest

from app.worker.providers.anthropic import AnthropicProvider
from app.worker.providers.base import ProviderError


def _antwort(text="fertig", *, stop="end_turn"):
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _leer_abgeschnitten():
    """Denken hat alles verbraucht: keine Textblöcke, stop_reason=max_tokens."""
    return {"content": [], "stop_reason": "max_tokens",
            "usage": {"input_tokens": 10, "output_tokens": 4096}}


class _Fake:
    """Sammelt die Bodies und liefert vorgegebene Antworten der Reihe nach."""

    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.bodies: list[dict] = []

    async def post(self, body, token):
        self.bodies.append(body)
        return self.antworten.pop(0)


async def _chat(monkeypatch, fake, **kw):
    prov = AnthropicProvider(model="claude-sonnet-5")
    monkeypatch.setattr(prov, "_post", fake.post)
    return await prov.chat(model="claude-sonnet-5", messages=[{"role": "user", "content": "hi"}],
                           auth_token="t", max_tokens=4096, **kw)


async def test_effort_geht_mit(monkeypatch):
    fake = _Fake(_antwort())
    await _chat(monkeypatch, fake, effort="medium")
    assert fake.bodies[0]["output_config"] == {"effort": "medium"}


async def test_ohne_effort_kein_feld(monkeypatch):
    """Leer heißt Anbieter-Standard — kein `output_config` mitschicken."""
    fake = _Fake(_antwort())
    await _chat(monkeypatch, fake)
    assert "output_config" not in fake.bodies[0]


async def test_abgeschnitten_wird_ohne_denken_gerettet(monkeypatch):
    fake = _Fake(_leer_abgeschnitten(), _antwort("<review-ok/>"))
    resp = await _chat(monkeypatch, fake, effort="medium")
    assert resp.text == "<review-ok/>"
    assert len(fake.bodies) == 2
    assert "thinking" not in fake.bodies[0]                       # erster Versuch wie gehabt
    assert fake.bodies[1]["thinking"] == {"type": "disabled"}     # zweiter ohne Denken
    assert fake.bodies[1]["max_tokens"] == 4096                   # Budget unverändert
    assert fake.bodies[1]["output_config"] == {"effort": "medium"}


async def test_hohe_stufe_faellt_beim_rettungsversuch_weg(monkeypatch):
    """`thinking: disabled` ist oberhalb `high` ein 400er — die Stufe muss weichen."""
    fake = _Fake(_leer_abgeschnitten(), _antwort())
    await _chat(monkeypatch, fake, effort="max")
    assert "output_config" not in fake.bodies[1]


async def test_zweimal_abgeschnitten_meldet_ehrlich(monkeypatch):
    fake = _Fake(_leer_abgeschnitten(), _leer_abgeschnitten())
    with pytest.raises(ProviderError) as err:
        await _chat(monkeypatch, fake)
    assert "auch ohne Denken" in str(err.value)
    assert err.value.retryable
    assert len(fake.bodies) == 2      # kein endloses Nachfassen


async def test_halbe_tool_argumente_zaehlen_als_abgeschnitten(monkeypatch):
    """Der zweite Fall: Text war schon da, aber der Tool-Aufruf blieb unvollständig."""
    halb = {"content": [{"type": "text", "text": "ich schaue mal"},
                        {"type": "tool_use", "id": "t1", "name": "mcp__fs_read", "input": {}}],
            "stop_reason": "max_tokens", "usage": {}}
    fake = _Fake(halb, _antwort())
    resp = await _chat(monkeypatch, fake)
    assert resp.text == "fertig"
    assert fake.bodies[1]["thinking"] == {"type": "disabled"}
