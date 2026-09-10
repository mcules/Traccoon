"""`POST /assistant/transcribe`: a recording in, its words out.

The containers are not part of this: `transcribe` is replaced, and what is pinned down is the
route around it, the limit before the body is read, and that a failure is an honest error
rather than an empty text.
"""
import pytest

from app.services import transcribe as speech
from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def test_a_recording_comes_back_as_text(client, db, monkeypatch):
    user = await make_user(db, "sprecher")
    seen = {}

    async def fake(audio: bytes, mediakind: str = "voice", mime_type=None) -> str:
        seen.update(audio=audio, mediakind=mediakind, mime_type=mime_type)
        return "was liegt heute an"

    monkeypatch.setattr(speech, "transcribe", fake)
    r = await client.post("/assistant/transcribe", headers=auth(user),
                          files={"audio": ("rec.m4a", b"fake-aac", "audio/mp4")})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "was liegt heute an"}
    # Handed on as an upload with its declared type: that is what picks the file name and
    # content type the whisper container reads the format from.
    assert seen == {"audio": b"fake-aac", "mediakind": "audio", "mime_type": "audio/mp4"}


async def test_too_large_is_refused_before_it_is_transcribed(client, db, monkeypatch):
    user = await make_user(db, "lang")
    monkeypatch.setattr(speech, "VOICE_MAX_BYTES", 8)

    async def never(*a, **k):
        raise AssertionError("must not be reached")

    monkeypatch.setattr(speech, "transcribe", never)
    r = await client.post("/assistant/transcribe", headers=auth(user),
                          files={"audio": ("rec.m4a", b"123456789", "audio/mp4")})
    assert r.status_code == 413
    assert r.json()["key"] == "err.audio_too_large"


async def test_a_failure_is_an_error_not_an_empty_text(client, db, monkeypatch):
    user = await make_user(db, "kaputt")

    async def broken(*a, **k):
        raise RuntimeError("container gone")

    monkeypatch.setattr(speech, "transcribe", broken)
    r = await client.post("/assistant/transcribe", headers=auth(user),
                          files={"audio": ("rec.m4a", b"x", "audio/mp4")})
    assert r.status_code == 502
    assert r.json()["key"] == "err.transcription_failed"


async def test_without_a_session_nobody_transcribes(client):
    r = await client.post("/assistant/transcribe", files={"audio": ("rec.m4a", b"x", "audio/mp4")})
    assert r.status_code == 401


def test_the_bot_still_calls_the_same_code():
    from app.bot import __main__ as bot_main

    assert bot_main._transcribe is speech.transcribe
    assert bot_main.VOICE_MAX_BYTES == speech.VOICE_MAX_BYTES
