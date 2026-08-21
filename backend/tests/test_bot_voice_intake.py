"""`_voice_transkript` is the way from a voice message to text, BEFORE the text runs on like
a normal plain text message (`_chat_auftrag`/`_reply`).

`bot` is deliberately an explicit parameter (not from the aiogram closure): only that way can
this function be tested without a real bot or network, following the pattern of `_zustellen`
in `test_bot_media.py`: FakeBot and FakeMessage instead of a network, small dummies instead of an ORM.
"""
import pytest

from app.bot.__main__ import _voice_transcript
import app.bot.__main__ as bot_main


class FakeFile:
    def __init__(self, file_path="voice/abc.oga"):
        self.file_path = file_path


class FakeBuffer:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self):
        return self._raw


class FakeBot:
    def __init__(self, raw: bytes = b"fake-ogg-bytes", load_error: Exception | None = None):
        self.raw = raw
        self.load_error = load_error

    async def get_file(self, file_id):
        if self.load_error:
            raise self.load_error
        return FakeFile()

    async def download_file(self, file_path):
        return FakeBuffer(self.raw)


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
        self.replies: list[str] = []

    async def answer(self, text, **kw):
        self.replies.append(text)


async def test_no_voice_message_yields_none():
    m = FakeMessage()
    assert await _voice_transcript(FakeBot(), m) is None


async def test_success_yields_a_transcript_and_reports_it_visibly(monkeypatch):
    async def fake_transcribe(audio, mediakind="voice", mime_type=None):
        assert audio == b"fake-ogg-bytes"
        assert mediakind == "voice"
        return "was liegt heute an"

    monkeypatch.setattr(bot_main, "_transcribe", fake_transcribe)
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transcript(FakeBot(), m)

    assert text == "was liegt heute an"
    assert any("understood" in a and "was liegt heute an" in a for a in m.replies)


async def test_too_long_a_duration_is_refused_without_transcribing(monkeypatch):
    def may_not_run(*a, **kw):
        raise AssertionError("the transcription should never have started")

    monkeypatch.setattr(bot_main, "_transcribe", may_not_run)
    monkeypatch.setattr(bot_main, "VOICE_MAX_SECONDS", 600)
    m = FakeMessage(voice=FakeMedia(duration=700, file_size=10_000))

    text = await _voice_transcript(FakeBot(), m)

    assert text == ""
    assert any("too long" in a for a in m.replies)


async def test_too_large_a_file_is_refused_without_transcribing(monkeypatch):
    def may_not_run(*a, **kw):
        raise AssertionError("the transcription should never have started")

    monkeypatch.setattr(bot_main, "_transcribe", may_not_run)
    monkeypatch.setattr(bot_main, "VOICE_MAX_BYTES", 1000)
    m = FakeMessage(voice=FakeMedia(duration=5, file_size=5_000_000))

    text = await _voice_transcript(FakeBot(), m)

    assert text == ""
    assert any("too large" in a for a in m.replies)


async def test_without_duration_and_size_it_refuses_instead_of_loading_unchecked():
    m = FakeMessage(voice=FakeMedia(duration=0, file_size=0))

    text = await _voice_transcript(FakeBot(), m)

    assert text == ""
    assert any("cannot be determined" in a for a in m.replies)


async def test_an_unloadable_file_is_refused_honestly():
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transcript(FakeBot(load_error=RuntimeError("kaputt")), m)

    assert text == ""
    assert any("could not be loaded" in a for a in m.replies)


async def test_a_transcription_error_is_refused_honestly_with_a_reason(monkeypatch):
    async def broken(audio, mediakind="voice", mime_type=None):
        raise RuntimeError("no WHISPER_URL configured")

    monkeypatch.setattr(bot_main, "_transcribe", broken)
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transcript(FakeBot(), m)

    assert text == ""
    assert any("transcription did not work" in a for a in m.replies)


async def test_an_empty_transcript_is_refused_honestly(monkeypatch):
    async def empty(audio, mediakind="voice", mime_type=None):
        return ""

    monkeypatch.setattr(bot_main, "_transcribe", empty)
    m = FakeMessage(voice=FakeMedia())

    text = await _voice_transcript(FakeBot(), m)

    assert text == ""
    assert any("could not hear any speech" in a for a in m.replies)


async def test_a_video_note_is_recognised_and_its_media_kind_passed_on(monkeypatch):
    seen = {}

    async def remember(audio, mediakind="voice", mime_type=None):
        seen["medienart"] = mediakind
        return "hallo"

    monkeypatch.setattr(bot_main, "_transcribe", remember)
    m = FakeMessage(video_note=FakeMedia())

    text = await _voice_transcript(FakeBot(), m)

    assert text == "hallo"
    assert seen["medienart"] == "video_note"
