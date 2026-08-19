"""Voice messages are transcribed locally (faster-whisper), with no cloud call.

What is tested is `_transkribieren` against a mock server (httpx MockTransport), following
the pattern of `test_destinations.py`: no real network, and every request can be inspected.
"""
import httpx
import pytest

from app.bot import __main__ as bot_main


def _mock(aufzeichnung: list, antworten: list[dict]):
    """httpx client that records requests and delivers the answers in order."""
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
    # The first attempt goes out with language=de: German is the most common case.
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


async def test_vokabular_geht_als_initial_prompt_mit(monkeypatch):
    """Proper names are the difference between usable and unusable.

    Measured on this host on 2026-08-07, the same sentence, the same model (large-v3-turbo):
    without a vocabulary list "Ticket Terra 1 und 30 in Trakon … Digist … Univer", with the
    list word for word "Ticket ABC-31 in Traccoon … Digest … GameProj".
    """
    import app.bot.__main__ as bot

    gesehen: list[dict] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "fertig"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, files=None):
            gesehen.append(dict(params or {}))
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(bot, "VOICE_VOKABULAR", "Traccoon, GameProj, ABC-31.")
    monkeypatch.setattr(bot, "_vokabular_cache", (0.0, ""))

    assert await bot._transkribieren(b"x", "voice", None) == "fertig"
    assert gesehen[0]["initial_prompt"] == "Traccoon, GameProj, ABC-31."


async def test_ohne_vokabular_kein_feld(monkeypatch):
    """Empty means off: no empty `initial_prompt` that only confuses the recognition."""
    import app.bot.__main__ as bot

    gesehen: list[dict] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "fertig"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, files=None):
            gesehen.append(dict(params or {}))
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    # Empty means: nothing by hand AND nothing in the database. The list has been built from
    # our own data since `_vokabular()`, so emptying the environment variable alone would
    # only check that the test database is empty, not the behaviour.
    async def _leer():
        return ""

    monkeypatch.setattr(bot, "VOICE_VOKABULAR", "")
    monkeypatch.setattr(bot, "_vokabular", _leer)

    await bot._transkribieren(b"x", "voice", None)
    assert "initial_prompt" not in gesehen[0]


async def test_vokabular_landet_im_prompt(monkeypatch):
    """And the other way round: what stands in the list Whisper gets to see. Without that it
    hears "Trakon" instead of "Traccoon", which is the whole reason for the list."""
    import app.bot.__main__ as bot

    gesehen: list[dict] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "fertig"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, files=None):
            gesehen.append(dict(params or {}))
            return _Resp()

    async def _worte():
        return "Traccoon, Ticket ABC-31"

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(bot, "_vokabular", _worte)

    await bot._transkribieren(b"x", "voice", None)
    assert gesehen[0]["initial_prompt"] == "Traccoon, Ticket ABC-31"


def test_asr_text_schaelt_die_steuermarken():
    """Qwen3-ASR writes its marks into the text; without the cut, "language German
    <asr_text>…" would stand as "🎙 understood" in the chat and go on to the assistant that way."""
    import app.bot.__main__ as bot

    assert bot._asr_text("language German<asr_text>Hallo Welt.") == "Hallo Welt."
    assert bot._asr_text("<asr_text>Hallo</asr_text>") == "Hallo"
    assert bot._asr_text("  schon sauber  ") == "schon sauber"


async def test_qwen_ist_erste_wahl_whisper_faengt_auf(monkeypatch):
    """The fallback is the point: losing a message would be more expensive than a slower
    recognition. If the GPU fails (container gone, model still loading), Whisper takes over."""
    import app.bot.__main__ as bot

    versuche: list[str] = []

    async def qwen_kaputt(audio, medienart, mime_type):
        versuche.append("qwen")
        raise RuntimeError("Connection refused")

    async def whisper_ok(*a, **k):
        versuche.append("whisper")
        return "über Whisper verstanden"

    monkeypatch.setattr(bot, "ASR_URL", "http://asr-gpu:9100")
    monkeypatch.setattr(bot, "_transkribieren_qwen", qwen_kaputt)
    monkeypatch.setattr(bot, "_vokabular", whisper_ok)   # only so that the Whisper path runs

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "über Whisper verstanden"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            versuche.append("whisper")
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    assert await bot._transkribieren(b"x", "voice", None) == "über Whisper verstanden"
    assert versuche[0] == "qwen" and "whisper" in versuche[1:]
