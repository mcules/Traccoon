"""The thinking must not eat the run up.

On sonnet-5/opus-5 adaptive thinking is ON as soon as the field `thinking` is missing, and it
shares `max_tokens` with the visible answer. On 2026-08-07 first the reviewer died of that
(run 744, 41 iterations) and then the developer (run 752, 29 iterations): budget used up in

Two layers of protection are nailed down here:
1. `effort` on the agent goes along as `output_config.effort`, the clean lever.
2. If an answer runs into `max_tokens` regardless, the provider tries it ONCE without
   thinking. Only when that is truncated as well does the turn die.
"""
import pytest

from app.worker.providers.anthropic import AnthropicProvider
from app.worker.providers.base import ProviderError


def _antwort(text="fertig", *, stop="end_turn"):
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop,
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _leer_abgeschnitten():
    """The thinking used everything up: no text blocks, stop_reason=max_tokens."""
    return {"content": [], "stop_reason": "max_tokens",
            "usage": {"input_tokens": 10, "output_tokens": 4096}}


class _Fake:
    """Collects the bodies and delivers given answers in order."""

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
    """Empty means the vendor default: send no `output_config` along."""
    fake = _Fake(_antwort())
    await _chat(monkeypatch, fake)
    assert "output_config" not in fake.bodies[0]


async def test_abgeschnitten_wird_ohne_denken_gerettet(monkeypatch):
    fake = _Fake(_leer_abgeschnitten(), _antwort("<review-ok/>"))
    resp = await _chat(monkeypatch, fake, effort="medium")
    assert resp.text == "<review-ok/>"
    assert len(fake.bodies) == 2
    assert "thinking" not in fake.bodies[0]                       # the first attempt as before
    assert fake.bodies[1]["thinking"] == {"type": "disabled"}     # the second without thinking
    assert fake.bodies[1]["max_tokens"] == 4096                   # the budget unchanged
    assert fake.bodies[1]["output_config"] == {"effort": "medium"}


async def test_hohe_stufe_faellt_beim_rettungsversuch_weg(monkeypatch):
    """`thinking: disabled` is a 400 above `high`: the level has to give way."""
    fake = _Fake(_leer_abgeschnitten(), _antwort())
    await _chat(monkeypatch, fake, effort="max")
    assert "output_config" not in fake.bodies[1]


async def test_zweimal_abgeschnitten_meldet_ehrlich(monkeypatch):
    fake = _Fake(_leer_abgeschnitten(), _leer_abgeschnitten())
    with pytest.raises(ProviderError) as err:
        await _chat(monkeypatch, fake)
    assert "auch ohne Denken" in str(err.value)
    assert err.value.retryable
    assert len(fake.bodies) == 2      # no endless following up


async def test_halbe_tool_argumente_zaehlen_als_abgeschnitten(monkeypatch):
    """The second case: text was already there, but the tool call stayed incomplete."""
    halb = {"content": [{"type": "text", "text": "ich schaue mal"},
                        {"type": "tool_use", "id": "t1", "name": "mcp__fs_read", "input": {}}],
            "stop_reason": "max_tokens", "usage": {}}
    fake = _Fake(halb, _antwort())
    resp = await _chat(monkeypatch, fake)
    assert resp.text == "fertig"
    assert fake.bodies[1]["thinking"] == {"type": "disabled"}
