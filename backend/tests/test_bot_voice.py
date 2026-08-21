"""Voice messages are transcribed locally (faster-whisper), with no cloud call.

What is tested is `_transkribieren` against a mock server (httpx MockTransport), following
the pattern of `test_destinations.py`: no real network, and every request can be inspected.
"""
import httpx
import pytest

from app.bot import __main__ as bot_main


def _mock(recording: list, replies: list[dict]):
    """httpx client that records requests and delivers the answers in order."""
    def handler(request: httpx.Request) -> httpx.Response:
        recording.append(request)
        answer = replies.pop(0) if len(replies) > 1 else replies[0]
        return httpx.Response(answer.get("status", 200), json=answer.get("json", {"text": ""}))
    transport = httpx.MockTransport(handler)

    class Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw.pop("timeout", None)
            super().__init__(*a, transport=transport, **kw)

    return Client


async def test_transcription_delivers_the_text(monkeypatch):
    recording: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient",
                        _mock(recording, [{"json": {"text": "was liegt heute an"}}]))

    text = await bot_main._transcribe(b"fake-ogg-bytes")

    assert text == "was liegt heute an"
    # The first attempt goes out with language=de: German is the most common case.
    assert recording[0].url.params.get("language") == "de"


async def test_an_empty_first_answer_tries_auto_detection(monkeypatch):
    recording: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient", _mock(
        recording, [{"json": {"text": ""}}, {"json": {"text": "hello there"}}]))

    text = await bot_main._transcribe(b"fake-bytes")

    assert text == "hello there"
    assert len(recording) == 2
    assert recording[0].url.params.get("language") == "de"
    assert "language" not in recording[1].url.params


async def test_no_result_yields_an_empty_string(monkeypatch):
    recording: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient",
                        _mock(recording, [{"json": {"text": ""}}]))

    text = await bot_main._transcribe(b"fake-bytes")

    assert text == ""


async def test_a_server_error_is_passed_on(monkeypatch):
    recording: list[httpx.Request] = []
    monkeypatch.setattr(httpx, "AsyncClient",
                        _mock(recording, [{"status": 500, "json": {}}]))

    with pytest.raises(Exception):
        await bot_main._transcribe(b"fake-bytes")


async def test_without_a_whisper_url_it_stops_at_once(monkeypatch):
    monkeypatch.setattr(bot_main, "WHISPER_URL", "")

    with pytest.raises(RuntimeError):
        await bot_main._transcribe(b"fake-bytes")


async def test_vocabulary_travels_as_the_initial_prompt(monkeypatch):
    """Proper names are the difference between usable and unusable.

    Measured on this host on 2026-08-07, the same sentence, the same model (large-v3-turbo):
    without a vocabulary list "Ticket Terra 1 und 30 in Trakon … Digist … Univer", with the
    list word for word "Ticket TRA-31 in Traccoon … Digest … UniWar".
    """
    import app.bot.__main__ as bot

    seen: list[dict] = []

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
            seen.append(dict(params or {}))
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(bot, "VOICE_VOCABULARY", "Traccoon, UniWar, TRA-31.")
    monkeypatch.setattr(bot, "_vocabulary_cache", (0.0, ""))

    assert await bot._transcribe(b"x", "voice", None) == "fertig"
    assert seen[0]["initial_prompt"] == "Traccoon, UniWar, TRA-31."


async def test_no_field_without_a_vocabulary(monkeypatch):
    """Empty means off: no empty `initial_prompt` that only confuses the recognition."""
    import app.bot.__main__ as bot

    seen: list[dict] = []

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
            seen.append(dict(params or {}))
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    # Empty means: nothing by hand AND nothing in the database. The list has been built from
    # our own data since `_vokabular()`, so emptying the environment variable alone would
    # only check that the test database is empty, not the behaviour.
    async def _empty():
        return ""

    monkeypatch.setattr(bot, "VOICE_VOCABULARY", "")
    monkeypatch.setattr(bot, "_vocabulary", _empty)

    await bot._transcribe(b"x", "voice", None)
    assert "initial_prompt" not in seen[0]


async def test_vocabulary_lands_in_the_prompt(monkeypatch):
    """And the other way round: what stands in the list Whisper gets to see. Without that it
    hears "Trakon" instead of "Traccoon", which is the whole reason for the list."""
    import app.bot.__main__ as bot

    seen: list[dict] = []

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
            seen.append(dict(params or {}))
            return _Resp()

    async def _words():
        return "Traccoon, Ticket TRA-31"

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(bot, "_vocabulary", _words)

    await bot._transcribe(b"x", "voice", None)
    assert seen[0]["initial_prompt"] == "Traccoon, Ticket TRA-31"


def test_asr_text_strips_the_control_markers():
    """Qwen3-ASR writes its marks into the text; without the cut, "language German
    <asr_text>…" would stand as "🎙 understood" in the chat and go on to the assistant that way."""
    import app.bot.__main__ as bot

    assert bot._asr_text("language German<asr_text>Hallo Welt.") == "Hallo Welt."
    assert bot._asr_text("<asr_text>Hallo</asr_text>") == "Hallo"
    assert bot._asr_text("  schon sauber  ") == "schon sauber"


async def test_qwen_is_first_choice_whisper_catches_the_rest(monkeypatch):
    """The fallback is the point: losing a message would be more expensive than a slower
    recognition. If the GPU fails (container gone, model still loading), Whisper takes over."""
    import app.bot.__main__ as bot

    attempts: list[str] = []

    async def qwen_broken(audio, mediakind, mime_type):
        attempts.append("qwen")
        raise RuntimeError("Connection refused")

    async def whisper_ok(*a, **k):
        attempts.append("whisper")
        return "über Whisper verstanden"

    monkeypatch.setattr(bot, "ASR_URL", "http://asr-gpu:9100")
    monkeypatch.setattr(bot, "_transcribe_qwen", qwen_broken)
    monkeypatch.setattr(bot, "_vocabulary", whisper_ok)   # only so that the Whisper path runs

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
            attempts.append("whisper")
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    assert await bot._transcribe(b"x", "voice", None) == "über Whisper verstanden"
    assert attempts[0] == "qwen" and "whisper" in attempts[1:]
