"""Sprachnachrichten werden lokal (faster-whisper) transkribiert — kein Cloud-Aufruf.

Getestet wird `_transkribieren` gegen einen Schein-Server (httpx MockTransport), nach dem
Muster von `test_destinations.py`: kein echtes Netz, jeder Request lässt sich nachsehen.
"""
import httpx
import pytest

from app.bot import __main__ as bot_main


def _mock(aufzeichnung: list, antworten: list[dict]):
    """httpx-Client, der Requests aufzeichnet und die Antworten der Reihe nach ausliefert."""
    def handler(request: httpx.Request) -> httpx.Response:
        aufzeichnung.append(request)
        antwort = antworten.pop(0) if len(antworten) > 1 else antworten[0]
        return httpx.Response(antwort.get("status", 200), json=antwort.get("json", {"text": ""}))
    transport = httpx.MockTransport(handler)

    class Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw.pop("timeout", None)
            super().__init__(*a, transport=transport, **kw)

    return Client


async def test_transkription_liefert_den_text(monkeypatch):
    aufzeichnung: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient",
                        _mock(aufzeichnung, [{"json": {"text": "was liegt heute an"}}]))

    text = await bot_main._transkribieren(b"fake-ogg-bytes")

    assert text == "was liegt heute an"
    # Erster Versuch geht mit language=de raus — Deutsch ist der häufigste Fall.
    assert aufzeichnung[0].url.params.get("language") == "de"


async def test_leere_erste_antwort_versucht_auto_erkennung(monkeypatch):
    aufzeichnung: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient", _mock(
        aufzeichnung, [{"json": {"text": ""}}, {"json": {"text": "hello there"}}]))

    text = await bot_main._transkribieren(b"fake-bytes")

    assert text == "hello there"
    assert len(aufzeichnung) == 2
    assert aufzeichnung[0].url.params.get("language") == "de"
    assert "language" not in aufzeichnung[1].url.params


async def test_kein_ergebnis_liefert_leeren_string(monkeypatch):
    aufzeichnung: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient",
                        _mock(aufzeichnung, [{"json": {"text": ""}}]))

    text = await bot_main._transkribieren(b"fake-bytes")

    assert text == ""


async def test_serverfehler_wird_weitergereicht(monkeypatch):
    aufzeichnung: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient",
                        _mock(aufzeichnung, [{"status": 500, "json": {}}]))

    with pytest.raises(Exception):
        await bot_main._transkribieren(b"fake-bytes")


async def test_ohne_whisper_url_bricht_sofort_ab(monkeypatch):
    monkeypatch.setattr(bot_main, "WHISPER_URL", "")

    with pytest.raises(RuntimeError):
        await bot_main._transkribieren(b"fake-bytes")
