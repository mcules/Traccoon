"""Speech to text, inside the house.

A recording never leaves the machine: Qwen3-ASR on the integrated GPU first, faster-whisper
on the CPU as the fallback, both containers of this stack. This used to live in the Telegram
bot, which was the only door a voice message came through. The phone app is the second one,
and a rule that is written twice is a rule that will apply at one door and not at the other,
so the code moved here and the bot keeps its names as aliases.

The environment variables keep their `TELEGRAM_VOICE_*` names: they are set in running
installations, and a rename would be a silent change of limits at the next deploy.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os

from sqlalchemy import select

from ..db import SessionLocal
from ..models.user import User

log = logging.getLogger("traccoon.transcribe")

# Voice messages: a local faster-whisper container (no cloud call, no audio leaves the house,
# a requirement of the user). `/asr` is the native endpoint of
# onerahmet/openai-whisper-asr-webservice with ASR_ENGINE=faster_whisper.
WHISPER_URL = os.getenv("WHISPER_URL", "http://whisper:9000")
# First choice: Qwen3-ASR on the integrated GPU (llama.cpp/Vulkan). Not a pure transcriber but
# a language model with audio input: it understands proper names you name in the prompt instead
# of merely brushing them. Measured on this host on 2026-08-07, 7 s of German speech:
#   faster-whisper (CPU, large-v3-turbo)  3.1 s  "ABC-31 in Traccoon"      ok
#   whisper.cpp    (GPU, large-v3-turbo)  0.7 s  "ABC-31 in Trakong"       wrong
#   Qwen3-ASR      (GPU, 1.7B Q8_0)       0.5 s  "ABC-31 in Traccoon"      ok
# Empty means off, and then everything runs through Whisper as before.
ASR_URL = os.getenv("ASR_URL", "").strip().rstrip("/")
# Ten minutes as the default limit: longer is unusual on a phone and would block the CPU
# container for a long time. Deliberately configurable instead of fixed, in case it proves tight.
VOICE_MAX_SECONDS = int(os.getenv("TELEGRAM_VOICE_MAX_SECONDS", "600"))
# A fallback upper bound over the file size in case Telegram delivers no `duration` (happens
# with some `audio` uploads without metadata); without it the length check would be useless
# then. A rough guide: OGG/Opus voice messages are about 1 MB per minute.
# IMPORTANT: the bot API (without a local bot API server of our own) refuses `getFile` and the
# download of ANY file above 20 MB. A default above that (25 MB, say) would let files between
# 20 and 25 MB pass the size check and then fail at the download with a technical exception,
# triggering the misleading "could not be loaded" message instead of the intended "too large"
# one. Hence a default of 19 MB, with a safety margin to the hard 20 MB limit.
# one. Hence a default of 19 MB, with a safety margin to the hard 20 MB limit.
VOICE_MAX_BYTES = int(os.getenv("TELEGRAM_VOICE_MAX_BYTES", str(19 * 1024 * 1024)))
# Extra words by hand, for everything that is NOT in the database (names from other stacks,
# technical terms, abbreviations). The normal case needs none of this: the list builds itself
# from our own data (see `vocabulary`).
VOICE_VOCABULARY = (os.getenv("TELEGRAM_VOICE_VOCABULARY")
                    or os.getenv("TELEGRAM_VOICE_VOKABULAR", "")).strip()
# Whisper cuts the `initial_prompt` at about 224 tokens and then takes the END, so a list that
# is too long loses exactly the words standing at the front. Better keep it short.
VOCABULARY_MAX_WORDS = int(os.getenv("TELEGRAM_VOICE_VOCABULARY_MAX")
                           or os.getenv("TELEGRAM_VOICE_VOKABULAR_MAX", "60"))
_vocabulary_cache: tuple[float, str] = (0.0, "")


async def vocabulary() -> str:
    """The proper names of this house, from the database instead of a maintained list.

    Whisper hears "Trakon" instead of "Traccoon" and "Terra 1 and 30" instead of a ticket key,
    because no language model can know these words. One has to tell it, but nobody should have
    to maintain a list for that: projects, ticket prefixes, agent roles and people are in the
    database already, and a new project brings its word along by itself.

    Cached for ten minutes: the names rarely change, and no voice message should
    drei Abfragen kosten.
    """
    global _vocabulary_cache
    alter, text = _vocabulary_cache
    now = asyncio.get_running_loop().time()
    if text and now - alter < 600:
        return text

    from ..models.agents import AgentDefinition
    from ..models.enums import UserStatus
    from ..models.project import Project
    words: list[str] = []
    try:
        async with SessionLocal() as db:
            for p in (await db.execute(select(Project))).scalars().all():
                # Both: the key is dictated letter by letter ("TRA 31"), the name is spoken.
                # A sample ticket teaches Whisper the spelling.
                words += [p.name, f"Ticket {p.key}-31"]
            for a in (await db.execute(
                    select(AgentDefinition.role).distinct())).scalars().all():
                words.append(a.replace("_", " "))
            for u in (await db.execute(select(User).where(
                    User.status == UserStatus.active))).scalars().all():
                words.append((u.display_name or u.username or "").strip())
    except Exception:  # noqa: BLE001 — transcribing without a vocabulary beats not at all
        log.exception("The vocabulary could not be built, the transcription runs without it")

    if VOICE_VOCABULARY:
        words += [w.strip() for w in VOICE_VOCABULARY.replace(".", ",").split(",")]
    seen: set[str] = set()
    clean = [w for w in words
              if w and len(w) > 1 and not (w.lower() in seen or seen.add(w.lower()))]
    text = ", ".join(clean[:VOCABULARY_MAX_WORDS]) + ("." if clean else "")
    _vocabulary_cache = (now, text)
    return text


# A whitelist of known audio containers for `audio` uploads (mime_type to file extension).
# `mime_type` is metadata filled in freely by the SENDING CLIENT in the message, not a verified
# server side property. If the raw value were passed on unchecked as the HTTP content type of
# the multipart part to the Whisper container, a prepared `mime_type` (control characters,
# arbitrary string) could end up there. So only known, harmless values pass through, and
# everything else falls back to a safe default.
_AUDIO_MIME_WHITELIST: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/webm": "weba",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


def upload_name_kind(mediakind: str, mime_type: str | None) -> tuple[str, str]:
    """File name and content type matching the actual media type, NOT `audio.ogg` /
    `application/octet-stream` across the board: `voice` really is OGG/Opus, but `audio`
    uploads are often MP3/M4A/WAV and `video_note` is an MP4 container (a video plus an audio
    track). If ffmpeg in the Whisper image does not recognise the format because of a wrong
    extension or content type, the transcription fails or delivers junk, and the user wrongly
    gets "no speech recognised" instead of the real cause.
    gets "no speech recognised" instead of the real cause.

    `mime_type` comes UNCHECKED from Telegram (ultimately from the sending client), so it is
    checked against `_AUDIO_MIME_WHITELIST` instead of taking the raw value as the HTTP content
    type. An unknown or suspicious value falls back to a safe default.
    """
    if mediakind == "video_note":
        return "video_note.mp4", "video/mp4"
    if mediakind == "audio":
        mime = (mime_type or "").strip().lower()
        extension = _AUDIO_MIME_WHITELIST.get(mime)
        if extension is None:
            # Not listed (an unknown format OR a manipulated value), so a safe default instead
            # of putting the raw value unchecked into the multipart header.
            return "audio.mp3", "audio/mpeg"
        return f"audio.{extension}", mime
    return "voice.ogg", "audio/ogg"


async def to_wav(audio: bytes) -> bytes:
    """Telegram-Audio in 16-kHz-Mono-WAV wandeln.

    The audio path of llama.cpp (miniaudio) takes WAV/MP3/FLAC, but Telegram delivers OGG/Opus,
    and the server answers that with "Failed to load image or audio file". ffmpeg reads
    everything Telegram sends (including the MP4 track of a video note), and 16 kHz mono is the
    format every ASR model works with anyway.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    wav, error = await proc.communicate(audio)
    if proc.returncode != 0 or not wav:
        raise RuntimeError(f"ffmpeg: {(error or b'').decode()[:200]}")
    return wav


def asr_text(raw: str) -> str:
    """Peel the payload out of the model answer.

    Qwen3-ASR writes its control markers into the text: "language German<asr_text>…". Without
    this cut that would stand in the chat as "🎙 understood" and go on to the
    Assistenten weiter.
    """
    text = raw.split("<asr_text>")[-1]
    for mark in ("</asr_text>", "<|im_end|>"):
        text = text.split(mark)[0]
    return text.strip()


async def transcribe_qwen(audio: bytes, mediakind: str, mime_type: str | None) -> str:
    """Qwen3-ASR on the integrated GPU, a language model with audio input.

    The difference to Whisper is the handling of proper names: Whisper gets a word list as
    priming text and weights it weakly, Qwen gets it as the context of a conversation. Measured
    on 2026-08-07 with the same recording: "ABC-31 in Trakong" (whisper.cpp/GPU)
    against "ABC-31 in Traccoon" (here), at 0.5 s instead of 3.1 s on the CPU.
    """
    import httpx
    wav = await to_wav(audio)
    words = await vocabulary()
    hint = f"Proper names that may occur: {vocabulary}\n" if vocabulary else ""
    body = {
        "model": "qwen3-asr", "temperature": 0, "max_tokens": 2048,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": hint +
             "Transcribe the voice message word for word, in the language it is spoken in. "
             "Give the text and nothing else."},
            {"type": "input_audio",
             "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}]}],
    }
    async with httpx.AsyncClient(timeout=max(120.0, VOICE_MAX_SECONDS + 60.0)) as client:
        resp = await client.post(f"{ASR_URL}/v1/chat/completions", json=body)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return asr_text(content)


async def transcribe(audio: bytes, mediakind: str = "voice",
                           mime_type: str | None = None) -> str:
    """Transcribe a voice message locally. German first (the most common case), and on an EMPTY
    result (not on a technical error) a second attempt without a language (auto detection). A
    4xx/5xx from the container or a connection error aborts at once, because transmitting the
    same file completely again would double the processing time of a long message without
    changing anything about the error.
    Empty after the first attempt means a second one; empty after both means an empty string,
    not an error. A real error is passed on so that the caller can decline honestly instead of
    staying silent.

    The timeout is tied to `VOICE_MAX_SECONDS` instead of being fixed: an allowed ten minute
    message really does need several minutes of transcription on the CPU (model "small"), and a
    fixed 120 s timeout would abort exactly the messages the length check allows. Factor 1.0 of
    the message length plus a 60 s base for model loading and overhead, at least 120 s for short
    messages.
    """
    import httpx
    if ASR_URL:
        # First choice: Qwen3-ASR on the GPU. If it fails (container gone, model still loading,
        # audio unreadable) it falls back to Whisper instead of losing the message: a second
        # path that already runs is worth more than an honest refusal.
        try:
            return await transcribe_qwen(audio, mediakind, mime_type)
        except Exception as exc:  # noqa: BLE001
            log.warning("Qwen3-ASR failed (%s), continuing with Whisper", exc)
    if not WHISPER_URL:
        raise RuntimeError("no WHISPER_URL configured (the local whisper container is missing)")
    timeout = max(120.0, VOICE_MAX_SECONDS + 60.0)
    filename, content_type = upload_name_kind(mediakind, mime_type)
    words = await vocabulary()
    async with httpx.AsyncClient(timeout=timeout) as client:
        for language in ("de", None):
            params = {"output": "json"}
            if language:
                params["language"] = language
            if words:
                # Whisper takes `initial_prompt` as priming text and aligns its word
                # expectations with it. For proper names that is THE lever, measured on this
                # host on 2026-08-07 with the same sentence and the same model:
                #   with:    "Ticket ABC-31 in Traccoon … Digest … a game"
                params["initial_prompt"] = words
            # A technical error (unreachable, rejected format, 4xx/5xx) is NOT caught but
            # passed through to the caller: a second attempt would only repeat the same error
            # and cost time on top.
            resp = await client.post(f"{WHISPER_URL}/asr", params=params,
                                     files={"audio_file": (filename, audio, content_type)})
            resp.raise_for_status()
            text = (resp.json().get("text") or "").strip()
            if text:
                return text
    return ""
