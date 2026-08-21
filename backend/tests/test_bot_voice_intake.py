"""`_voice_transkript` is the way from a voice message to text, BEFORE the text runs on like
a normal plain text message (`_chat_auftrag`/`_reply`).

`bot` is deliberately an explicit parameter (not from the aiogram closure): only that way can
this function be tested without a real bot or network, following the pattern of `_zustellen`
in `test_bot_media.py`: FakeBot and FakeMessage instead of a network, small dummies instead of an ORM.
"""
import pytest

from app.bot.__main__ import _voice_transkript
import app.bot.__main__ as bot_main


class FakeFile:
    def __init__(self, file_path="voice/abc.oga"):
        self.file_path = file_path


class FakeBuffer:
    def __init__(self, roh: bytes):
        self._roh = roh

    def read(self):
        return self._roh


class FakeBot:
    def __init__(self, roh: bytes = b"fake-ogg-bytes", lade_error: Exception | None = None):
        self.roh = roh
        self.lade_error = lade_error

    async def get_file(self, file_id):
        if self.lade_error:
            raise self.lade_error
        return FakeFile()

    async def download_file(self, file_path):
        return FakeBuffer(self.roh)


class FakeMedia:
    def __init__(self, duration=5, file_size=10_000, mime_type=None, file_id="f1"):
        self.duration = duration
        self.file_size = file_size
        self.mime_type = mime_type
        self.file_id = file_id


class FakeMessage:
    """Only `voice`/`audio`/`video_note` plus `answer`; the rest does not interest `_voice_transkript`."""

    def __init__(self, voice=None, audio=None, video_note=None):
        self.voice = voice
        self.audio = audio
        self.video_note = video_note
        self.antworten: list[str] = []

    async def answer(self, text, **kw):
        self.antworten.append(text)


async def test_no_voice_message_yields_none():
    m = FakeMessage()
    assert await _voice_transkript(FakeBot(), m) is None


async def test_success_yields_a_transcript_and_reports_it_visibly(monkeypatch):
    async def fake_transkribieren(audio, medienart="voice", mime_type=None):
        assert audio == b"fake-ogg-bytes"
        assert medienart == "voice"
        return "was liegt heute an"

    monkeypatch.setattr(bot_main, "_transkribieren", fake_transkribieren)
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transkript(FakeBot(), m)

    assert text == "was liegt heute an"
    assert any("verstanden" in a and "was liegt heute an" in a for a in m.antworten)


async def test_too_long_a_duration_is_refused_without_transcribing(monkeypatch):
    def may_nicht_laufen(*a, **kw):
        raise AssertionError("the transcription should never have started")

    monkeypatch.setattr(bot_main, "_transkribieren", may_nicht_laufen)
    monkeypatch.setattr(bot_main, "VOICE_MAX_SECONDS", 600)
    m = FakeMessage(voice=FakeMedia(duration=700, file_size=10_000))

    text = await _voice_transkript(FakeBot(), m)

    assert text == ""
    assert any("zu lang" in a for a in m.antworten)


async def test_too_large_a_file_is_refused_without_transcribing(monkeypatch):
    def may_nicht_laufen(*a, **kw):
        raise AssertionError("the transcription should never have started")

    monkeypatch.setattr(bot_main, "_transkribieren", may_nicht_laufen)
    monkeypatch.setattr(bot_main, "VOICE_MAX_BYTES", 1000)
    m = FakeMessage(voice=FakeMedia(duration=5, file_size=5_000_000))

    text = await _voice_transkript(FakeBot(), m)

    assert text == ""
    assert any("zu groß" in a for a in m.antworten)


async def test_without_duration_and_size_it_refuses_instead_of_loading_unchecked():
    m = FakeMessage(voice=FakeMedia(duration=0, file_size=0))

    text = await _voice_transkript(FakeBot(), m)

    assert text == ""
    assert any("nicht bestimmbar" in a for a in m.antworten)


async def test_an_unloadable_file_is_refused_honestly():
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transkript(FakeBot(lade_error=RuntimeError("kaputt")), m)

    assert text == ""
    assert any("nicht geladen" in a for a in m.antworten)


async def test_a_transcription_error_is_refused_honestly_with_a_reason(monkeypatch):
    async def kaputt(audio, medienart="voice", mime_type=None):
        raise RuntimeError("no WHISPER_URL configured")

    monkeypatch.setattr(bot_main, "_transkribieren", kaputt)
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transkript(FakeBot(), m)

    assert text == ""
    assert any("Transkription nicht möglich" in a for a in m.antworten)


async def test_an_empty_transcript_is_refused_honestly(monkeypatch):
    async def leer(audio, medienart="voice", mime_type=None):
        return ""

    monkeypatch.setattr(bot_main, "_transkribieren", leer)
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transkript(FakeBot(), m)

    assert text == ""
    assert any("keine Sprache erkennen" in a for a in m.antworten)


async def test_a_video_note_is_recognised_and_its_media_kind_passed_on(monkeypatch):
    seen = {}

    async def merken(audio, medienart="voice", mime_type=None):
        seen["medienart"] = medienart
        return "hallo"

    monkeypatch.setattr(bot_main, "_transkribieren", merken)
    m = FakeMessage(video_note=FakeMedia())

    text = await _voice_transkript(FakeBot(), m)

    assert text == "hallo"
    assert seen["medienart"] == "video_note"
