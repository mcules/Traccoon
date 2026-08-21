"""Traccoon chat bot (aiogram v3). Shares the backend image (python -m app.bot).

Notifier poll (Notification to chat, with media when `media_path` is set),
Reply→Kommentar (geteilte apply_user_comment),
voice messages (voice/audio/video_note, local transcription, then the same path as text),
Inline-Buttons (approve/reject/accept/perm), Commands /tasks /comment.
A no-op (a stable sleep) when no TELEGRAM_BOT_TOKEN is set.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
import os
import re

from sqlalchemy import select

from ..db import SessionLocal
from ..models.assistant import AssistantTask, SpamVerdict
from ..models.enums import GlobalRole, TicketAgentStatus
from ..models.notification import Notification
from ..models.ops import PermAction, PermGrant, Permission, PermRequest
from ..models.ticket import Issue
from ..models.user import User
from ..services.assistant_inbox import (
    approve_assistant_task, create_chat_task, reject_assistant_task,
)
from ..services.artifacts import set_ticket_status
from ..services.comments import add_system_comment, apply_user_comment
from ..services.spam_review import decide_batch, decide, karte, reclaim
from ..worker.assistant_gate import apply_perm_decision
from .mdtg import clip, safe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("traccoon.bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "")

# Voice messages: a local faster-whisper container (no cloud call, no audio leaves the house,
# a requirement of the user). `/asr` is the native endpoint of
# onerahmet/openai-whisper-asr-webservice with ASR_ENGINE=faster_whisper.
WHISPER_URL = os.getenv("WHISPER_URL", "http://whisper:9000")
# First choice: Qwen3-ASR on the integrated GPU (llama.cpp/Vulkan). Not a pure transcriber but
# a language model with audio input: it understands proper names you name in the prompt instead
# of merely brushing them. Measured on this host on 2026-08-07, 7 s of German speech:
#   faster-whisper (CPU, large-v3-turbo)  3,1 s  „ABC-31 in Traccoon"      ✅
#   whisper.cpp    (GPU, large-v3-turbo)  0,7 s  „ABC-31 in Trakong"       ✗
#   Qwen3-ASR      (GPU, 1.7B Q8_0)       0,5 s  „ABC-31 in Traccoon"      ✅
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
# from our own data (see `_vokabular`).
VOICE_VOCABULARY = (os.getenv("TELEGRAM_VOICE_VOCABULARY")
                    or os.getenv("TELEGRAM_VOICE_VOKABULAR", "")).strip()
# Whisper cuts the `initial_prompt` at about 224 tokens and then takes the END, so a list that
# is too long loses exactly the words standing at the front. Better keep it short.
VOCABULARY_MAX_WORDS = int(os.getenv("TELEGRAM_VOICE_VOCABULARY_MAX")
                           or os.getenv("TELEGRAM_VOICE_VOKABULAR_MAX", "60"))
_vocabulary_cache: tuple[float, str] = (0.0, "")


async def _vocabulary() -> str:
    """The proper names of this house, from the database instead of a maintained list.

    Whisper hears "Trakon" instead of "Traccoon" and "Terra 1 and 30" instead of "ABC-31",
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


def _upload_name_kind(mediakind: str, mime_type: str | None) -> tuple[str, str]:
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


async def _to_wav(audio: bytes) -> bytes:
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


def _asr_text(raw: str) -> str:
    """Peel the payload out of the model answer.

    Qwen3-ASR writes its control markers into the text: "language German<asr_text>…". Without
    this cut that would stand in the chat as "🎙 understood" and go on to the
    Assistenten weiter.
    """
    text = raw.split("<asr_text>")[-1]
    for mark in ("</asr_text>", "<|im_end|>"):
        text = text.split(mark)[0]
    return text.strip()


async def _transcribe_qwen(audio: bytes, mediakind: str, mime_type: str | None) -> str:
    """Qwen3-ASR on the integrated GPU, a language model with audio input.

    The difference to Whisper is the handling of proper names: Whisper gets a word list as
    priming text and weights it weakly, Qwen gets it as the context of a conversation. Measured
    on 2026-08-07 with the same recording: "ABC-31 in Trakong" (whisper.cpp/GPU)
    against "ABC-31 in Traccoon" (here), at 0.5 s instead of 3.1 s on the CPU.
    """
    import httpx
    wav = await _to_wav(audio)
    vocabulary = await _vocabulary()
    hint = f"Eigennamen, die vorkommen können: {vocabulary}\n" if vocabulary else ""
    body = {
        "model": "qwen3-asr", "temperature": 0, "max_tokens": 2048,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": hint +
             "Transkribiere die Sprachnachricht wörtlich auf Deutsch. Gib nur den Text aus."},
            {"type": "input_audio",
             "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}]}],
    }
    async with httpx.AsyncClient(timeout=max(120.0, VOICE_MAX_SECONDS + 60.0)) as client:
        resp = await client.post(f"{ASR_URL}/v1/chat/completions", json=body)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return _asr_text(content)


async def _transcribe(audio: bytes, mediakind: str = "voice",
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
            return await _transcribe_qwen(audio, mediakind, mime_type)
        except Exception as exc:  # noqa: BLE001
            log.warning("Qwen3-ASR failed (%s), continuing with Whisper", exc)
    if not WHISPER_URL:
        raise RuntimeError("no WHISPER_URL configured (the local whisper container is missing)")
    timeout = max(120.0, VOICE_MAX_SECONDS + 60.0)
    filename, content_type = _upload_name_kind(mediakind, mime_type)
    vocabulary = await _vocabulary()
    async with httpx.AsyncClient(timeout=timeout) as client:
        for language in ("de", None):
            params = {"output": "json"}
            if language:
                params["language"] = language
            if vocabulary:
                # Whisper takes `initial_prompt` as priming text and aligns its word
                # expectations with it. For proper names that is THE lever, measured on this
                # host on 2026-08-07 with the same sentence and the same model:
                #   with:    "Ticket ABC-31 in Traccoon … Digest … GameProj"
                params["initial_prompt"] = vocabulary
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


# Decisions of the approval buttons in plain words, for the note on the message.
_DEC_TEXT = {"once": "einmal", "always": "immer", "never": "nie"}


async def _done(cq: CallbackQuery, note: str) -> None:
    """Remove the keyboard and write the outcome onto the question.

    That way one sees in the history at once what is still open: answered questions carry no
    buttons any more but a line with the decision and the time. Even on "already handled"
    (decided elsewhere) the buttons have to go, otherwise they keep inviting a press.
    # buttons any more but a line with the decision and the time.
    """
    msg = cq.message
    if msg is None:
        return
    line = f"<i>{safe(note)} · {_now().strftime('%d.%m. %H:%M')}</i>"
    try:
        # html_text keeps the formatting of the original message (bold, lines).
        await msg.edit_text(f"{msg.html_text}\n\n{line}", parse_mode="HTML")
        return
    except Exception:  # noqa: BLE001
        # Too old to edit, without text (a photo) or unchanged: then at least clear the
        # buttons away, which is the actual purpose.
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            log.warning("Could not remove the keyboard on message %s", msg.message_id)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# --- Medienausgang ---------------------------------------------------------------------
# This process is the ONLY way to the messenger: the backend container lacks
# `TELEGRAM_BOT_TOKEN` entirely (it only has `TELEGRAM_OWNER_CHAT`), and a send call to
# Telegram exists in the whole backend in exactly one place, this one. Whoever wants to send a
# file along puts it at a path visible to backend AND chat bot and writes it into
# `Notification.media_path`. A second exit would be the kind of duplication this repository
# argues against everywhere else.

def _subblocks_end(raw: bytes, i: int) -> int:
    """End of a chain of GIF sub blocks (length byte, data, …, 0x00)."""
    while i < len(raw) and raw[i]:
        i += raw[i] + 1
    return i + 1


def _gif_mass(raw: bytes) -> dict[str, int]:
    """Width, height and duration from the GIF itself, an empty dict when it is none.

    Why measure at all instead of entering fixed values: this exit knows only "a file", not its
    sender. Fixed dimensions would be a claim about content the notifier must not know, and at
    the first different format a lie would stand in the code.
    Why it is worth it: Telegram sizes the bubble from exactly these values BEFORE the file is
    loaded. Without them the layout jumps when it arrives.
    Unreadable or not a GIF means claiming nothing, and Telegram measures for itself after loading.
    """
    try:
        if not raw.startswith(b"GIF") or len(raw) < 13:
            return {}
        width = int.from_bytes(raw[6:8], "little")
        height = int.from_bytes(raw[8:10], "little")
        i = 13
        if raw[10] & 0x80:                       # skip the global colour table
            i += 3 * (2 ** ((raw[10] & 7) + 1))
        hundredth = 0
        while i < len(raw):
            mark = raw[i]
            if mark == 0x21:                    # Erweiterung
                label = raw[i + 1]
                i += 2
                if label == 0xF9 and i + 4 <= len(raw):
                    # Graphic control: block size, flags, delay (1/100 s).
                    hundredth += int.from_bytes(raw[i + 2:i + 4], "little")
                i = _subblocks_end(raw, i)
            elif mark == 0x2C:                  # Bildbeschreibung
                marker = raw[i + 9]
                i += 10
                if marker & 0x80:           # lokale Farbtabelle
                    i += 3 * (2 ** ((marker & 7) + 1))
                i += 1                           # LZW minimum code size
                i = _subblocks_end(raw, i)
            else:                                # 0x3B (end) or something unexpected
                break
        masse = {"width": width, "height": height}
        if hundredth:
            # Telegram wants whole seconds; a film under one second is still one.
            masse["duration"] = max(1, round(hundredth / 100))
        return masse
    except Exception:  # noqa: BLE001
        return {}


async def _deliver(bot, n, text: str, markup) -> None:
    """Deliver one notification, with media when some lies there, otherwise as text.

    Three decisions come together here:
    * `send_animation` and not `send_video` (which forces sound container semantics) and not
      `send_photo` (a still image). APNG is out anyway: Telegram shows it not as an animation
      but only as a sticker.
    * A missing file falls back to text silently. A film that is not there must not swallow a
      message: the message is the purpose, the medium the extra. (The most common cause: the
      `./data/film` mount is missing in THIS service.)
    * `notified_at` is ALWAYS set, including on failure. It is the only brake of the poller;
      without it the same row is retried every three seconds forever.
    The keyboard goes along unchanged on both paths: the buttons (approve, reject, accept,
    permissions) are just as valid on media as on text.
    """
    try:
        path = n.media_path or ""
        if path and os.path.isfile(path):
            from aiogram.types import BufferedInputFile
            with open(path, "rb") as fh:
                raw = fh.read()
            file = BufferedInputFile(raw, filename=os.path.basename(path))
            # Telegram cuts captions at 1024 characters: uncut, the message is rejected instead
            # of merely trimmed.
            label = text[:1024]
            kind = (n.media_kind or "").strip() or "animation"
            if kind == "photo":
                await bot.send_photo(int(n.chat_id), photo=file, caption=label,
                                     parse_mode="HTML", reply_markup=markup)
            elif kind == "document":
                await bot.send_document(int(n.chat_id), document=file, caption=label,
                                        parse_mode="HTML", reply_markup=markup)
            else:
                await bot.send_animation(int(n.chat_id), animation=file, caption=label,
                                         parse_mode="HTML", reply_markup=markup,
                                         **_gif_mass(raw))
        else:
            if path:
                log.warning("Medium %s not readable, notification %s goes as text", path, n.id)
            await bot.send_message(int(n.chat_id), text, parse_mode="HTML", reply_markup=markup)
    except Exception:  # noqa: BLE001
        log.exception("Sending to %s failed", n.chat_id)
    finally:
        n.notified_at = _now()


async def _acting_user(db, chat_id: str) -> User | None:
    u = (await db.execute(select(User).where(User.telegram_chat_id == str(chat_id)))).scalar_one_or_none()
    if u:
        return u
    return (await db.execute(select(User).where(User.global_role == GlobalRole.admin).order_by(User.id))).scalars().first()


async def _voice_transcript(bot, m) -> str | None:
    """Resolve a voice message (voice/audio/video_note) into text, including visible feedback so
    that mishearings are noticed at once (the lesson of 2026-07-29: better an honest refusal
    than silence).

    Returns None when `m` is not a voice message at all (the caller carries on normally).
    "" means a voice message that could not be processed, and this function has ALREADY sent
    the refusal with a reason, so the caller simply stops. A non empty string is the transcript,
    to be passed on exactly like incoming text.

    `bot` as an explicit parameter (instead of reaching into the closure of `run_bot()`),
    because otherwise this function would only be testable with a real aiogram bot, the
    Muster `_zustellen`/`_gif_masse`.
    """
    media = m.voice or m.audio or m.video_note
    if media is None:
        return None
    duration = getattr(media, "duration", 0) or 0
    size = getattr(media, "file_size", 0) or 0
    # Both signals are to be checked INDEPENDENTLY, not as alternatives: a short `duration`
    # (possibly forged or simply wrong) on an actually very large `file_size` must not skip the
    # size check, otherwise exactly the memory and CPU risk this check prevents would occur.

    if duration and duration > VOICE_MAX_SECONDS:
        await m.answer(f"🙉 Sprachnachricht zu lang ({duration // 60} Min., Grenze "
                       f"{VOICE_MAX_SECONDS // 60} Min.) — bitte kürzer aufnehmen oder "
                       f"als Text schicken.")
        return ""
    if size and size > VOICE_MAX_BYTES:
        await m.answer(f"🙉 Datei zu groß ({size // (1024 * 1024)} MB, Grenze "
                       f"{VOICE_MAX_BYTES // (1024 * 1024)} MB) — bitte kürzer "
                       f"aufnehmen oder als Text schicken.")
        return ""
    if not duration and not size:
        # Neither `duration` nor `file_size` usable. WITHOUT one of the two checks a file of
        # arbitrary size would be loaded completely into memory unchecked and passed on to
        # Whisper (a memory and CPU risk). Better an honest refusal than loading blindly.

        await m.answer("🙉 Länge/Größe der Datei nicht bestimmbar — bitte als Text "
                       "schicken oder als reguläre Sprachnachricht erneut aufnehmen.")
        return ""
    try:
        file = await bot.get_file(media.file_id)
        buffer = await bot.download_file(file.file_path)
        raw = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
    except Exception as exc:  # noqa: BLE001
        log.warning("Voice message %s not loadable: %s", media.file_id, exc)
        await m.answer("🙉 Sprachnachricht konnte nicht geladen werden — bitte als Text schicken.")
        return ""
    mediakind = "voice" if m.voice else ("video_note" if m.video_note else "audio")
    mime_type = getattr(media, "mime_type", None)
    try:
        text = await _transcribe(raw, mediakind=mediakind, mime_type=mime_type)
    except Exception as exc:  # noqa: BLE001
        log.warning("Transcription failed: %s", exc)
        await m.answer(f"🙉 Transkription nicht möglich ({exc}) — bitte als Text schicken.")
        return ""
    if not text:
        await m.answer("🙉 Ich konnte darin keine Sprache erkennen — bitte als Text schicken.")
        return ""
    # Sent raw (without `safe()`/HTML escaping): `safe()` escapes and converts markdown like
    # sequences into <b>/<i>/<code> tags, but this bot runs WITHOUT `parse_mode="HTML"`
    # (neither per call nor as a bot default). An HTML escaped string would then arrive
    # unparsed: `&`/`<`/`>` would appear as literal entities and converted tags as visible
    # `<b>…</b>` text instead of bold. No formatting is wanted here anyway, only the plain
    # transcript, cut to Telegram's length.
    await m.answer(f"🎙 verstanden: {clip(text)}")
    return text


async def run_bot() -> None:
    if not TOKEN:
        log.warning("No TELEGRAM_BOT_TOKEN, the bot stays idle.")
        while True:
            await asyncio.sleep(3600)

    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

    bot = Bot(TOKEN)
    dp = Dispatcher()

    async def _allowed(uid: int) -> bool:
        # Allowed: the env bootstrap (TELEGRAM_ALLOWED_IDS) OR any user who stored their chat
        # id in the web interface. A database lookup per call means new users take effect at
        # once, without a bot restart or an env edit.
        if uid in ALLOWED:
            return True
        async with SessionLocal() as db:
            u = (await db.execute(
                select(User).where(User.telegram_chat_id == str(uid)))).scalar_one_or_none()
            return u is not None

    def _kb_for(kind: str, issue_key: str, req_id: int | None) -> InlineKeyboardMarkup | None:
        if kind == "plan_review":
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Freigeben", callback_data=f"approve:{issue_key}"),
                InlineKeyboardButton(text="✖ Ablehnen", callback_data=f"reject:{issue_key}")]])
        if kind == "to_test":
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Abnehmen", callback_data=f"accept:{issue_key}")]])
        if kind == "blocked" and req_id:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Einmal", callback_data=f"perm:once:{req_id}"),
                InlineKeyboardButton(text="Immer", callback_data=f"perm:always:{req_id}"),
                InlineKeyboardButton(text="Nie", callback_data=f"perm:never:{req_id}")]])
        return None

    def _atask_kb(tid: int) -> InlineKeyboardMarkup:
        # Approval of an assistant item. The quick approval is redacted (safe); unredacted and
        # finer choices are handled by the web inbox.
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Freigeben", callback_data=f"atask:approve:{tid}"),
             InlineKeyboardButton(text="❌ Verwerfen", callback_data=f"atask:reject:{tid}")],
            [InlineKeyboardButton(text="♾️ Immer Absender", callback_data=f"atask:sender:{tid}"),
             InlineKeyboardButton(text="♾️ Immer Kategorie", callback_data=f"atask:category:{tid}")]])

    def _spam_kb(vid: int) -> InlineKeyboardMarkup:
        # Exactly two buttons. The question is a yes or no question, and every further option
        # ("later", "create a rule") would delay the answer that is needed for learning.
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ist Spam", callback_data=f"spam:yes:{vid}"),
            InlineKeyboardButton(text="🚫 Kein Spam", callback_data=f"spam:no:{vid}")]])

    def _spam_undo_kb(vid: int) -> InlineKeyboardMarkup:
        # Exactly one button: the mail is gone already, only the objection remains. "Fine as it
        # is" needs none, because silence is the consent.
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ Zurückholen", callback_data=f"spamundo:{vid}")]])

    def _spam_digest_kb(batch: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Alle sind Spam", callback_data=f"spamall:yes:{batch}"),
             InlineKeyboardButton(text="🚫 Keiner ist Spam", callback_data=f"spamall:no:{batch}")],
            [InlineKeyboardButton(text="👉 Einzeln durchgehen",
                                  callback_data=f"spamall:einzeln:{batch}")]])

    def _aperm_kb(tid: int) -> InlineKeyboardMarkup:
        # Tool approval of the assistant (once, always, never).
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Einmal", callback_data=f"aperm:once:{tid}"),
            InlineKeyboardButton(text="Immer", callback_data=f"aperm:always:{tid}"),
            InlineKeyboardButton(text="Nie", callback_data=f"aperm:never:{tid}")]])

    async def notifier() -> None:
        while True:
            try:
                async with SessionLocal() as db:
                    rows = (
                        await db.execute(select(Notification).where(
                            Notification.chat_id.isnot(None), Notification.notified_at.is_(None))
                            .order_by(Notification.id).limit(10))
                    ).scalars().all()
                    for n in rows:
                        issue_key = ""
                        req_id = None
                        if n.issue_id:
                            iss = await db.get(Issue, n.issue_id)
                            issue_key = iss.key if iss else ""
                            if n.kind == "blocked":
                                pr = (await db.execute(select(PermRequest).where(
                                    PermRequest.issue_id == n.issue_id, PermRequest.status == "pending")
                                    .order_by(PermRequest.id.desc()))).scalars().first()
                                req_id = pr.id if pr else None
                        text = f"<b>{safe(n.title)}</b>\n{safe(n.body)}" + (f"\n[{issue_key}]" if issue_key else "")
                        if n.kind == "assistant_review" and n.assistant_task_id:
                            markup = _atask_kb(n.assistant_task_id)
                        elif n.kind == "assistant_perm" and n.assistant_task_id:
                            markup = _aperm_kb(n.assistant_task_id)
                        elif n.kind == "spam_review" and n.spam_verdict_id:
                            markup = _spam_kb(n.spam_verdict_id)
                        elif n.kind == "spam_auto" and n.spam_verdict_id:
                            markup = _spam_undo_kb(n.spam_verdict_id)
                        elif n.kind == "spam_digest" and n.spam_verdict_id:
                            # The collection hangs on the first case: through it the key of the
                            # whole set is found.
                            first = await db.get(SpamVerdict, n.spam_verdict_id)
                            markup = (_spam_digest_kb(first.digest_batch)
                                      if first and first.digest_batch else None)
                        else:
                            markup = _kb_for(n.kind, issue_key, req_id)
                        # `_zustellen` decides between text and media, and sets `notified_at`
                        # in every case (otherwise an endless retry).
                        await _deliver(bot, n, text, markup)
                    await db.commit()
            except Exception:  # noqa: BLE001
                log.exception("notifier error")
            await asyncio.sleep(3)

    async def _agent_roles(db, user_id: int | None) -> list[str]:
        """The agents this person can address.

        Out of the database instead of a list in the code: whoever creates an agent should be
        able to reach it in the chat as well, without anyone touching the bot. The personal
        assistant drops out — it is the normal case and needs no prefix in front of it.
        """
        from ..models.agents import AgentDefinition
        if user_id is None:
            return []
        rows = (await db.execute(select(AgentDefinition.role).where(
            AgentDefinition.user_id == user_id,
            AgentDefinition.project_id.is_(None)))).scalars().all()
        return sorted({r for r in rows if r and r != "assistent"})

    @dp.message(Command("start"))
    async def _start(m: Message):
        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            roles = await _agent_roles(db, user.id if user else None)
        agents = " \u00b7 /agent <" + "|".join(roles) + "> <Text>" if roles else ""
        await m.answer("\U0001f99d Traccoon-Bot. /tasks \u00b7 /comment <KEY> <Text>" + agents)

    @dp.message(Command("tasks"))
    async def _tasks(m: Message):
        if not await _allowed(m.from_user.id):
            return
        async with SessionLocal() as db:
            rows = (await db.execute(select(Issue).where(Issue.assigned_agent.isnot(None))
                    .order_by(Issue.updated_at.desc()).limit(15))).scalars().all()
        if not rows:
            await m.answer("Keine zugewiesenen Tickets.")
            return
        await m.answer("\n".join(f"[{i.key}] {i.agent_status} — {i.summary}" for i in rows))

    @dp.message(Command("comment"))
    async def _comment(m: Message):
        if not await _allowed(m.from_user.id):
            return
        parts = (m.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await m.answer("Nutzung: /comment KEY Text")
            return
        key, text = parts[1], parts[2]
        async with SessionLocal() as db:
            iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
            if iss is None:
                await m.answer("Ticket nicht gefunden.")
                return
            user = await _acting_user(db, m.chat.id)
            await apply_user_comment(db, iss, text, user.id if user else None, "Telegram")
        await m.answer(f"Kommentar zu {key} gespeichert.")

    @dp.message(Command("agent"))
    async def _agent_chat(m: Message):
        """Talk to a named agent instead of to the personal assistant.

        This used to be one handler per agent — `/gameproj` for exactly one operator, built into
        the bot. A second agent would have needed a second handler and a deployment. Now the
        name is an argument, and which names exist comes out of the agents this person
        actually has.

        The way is the same as with `_assistant_chat`, only with meta.agent set:
        `_handle_assistant_task` resolves the agent from it (otherwise it falls back to
        'assistent' zurueck).
        """
        if not await _allowed(m.from_user.id):
            return
        words = (m.text or "").split(maxsplit=2)
        name = words[1].strip().lower() if len(words) > 1 else ""
        text = words[2].strip() if len(words) > 2 else ""

        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            roles = await _agent_roles(db, user.id if user else None)
            if not name or name not in roles:
                known = ", ".join(roles) or "keine"
                await m.answer("Nutzung: /agent <name> <Frage oder Auftrag>\n"
                               f"Verfuegbar: {known}")
                return
            if not text:
                await m.answer(f"Nutzung: /agent {name} <Frage oder Auftrag>")
                return
            await create_chat_task(db, user.id if user else None, text, str(m.chat.id),
                                   agent=name)
        await m.answer("\U0001f6f0 \u2026")

    @dp.message(F.reply_to_message)
    async def _reply(m: Message):
        if not await _allowed(m.from_user.id):
            return
        rt = m.reply_to_message.text or m.reply_to_message.html_text or ""

        # An answer by voice message: the same quoted reference as with text, only transcribed.
        text = m.text
        if text is None:
            belongs = await _voice_transcript(bot, m)
            if belongs is None:
                await m.answer("🙉 Damit kann ich noch nichts anfangen — schick es mir als "
                               "Text oder Sprachnachricht.")
                return
            if not belongs:
                return   # _voice_transkript has already sent the refusal
            text = belongs

        # Banking two factor: an answer to the "banking sync needs a 2FA code for <source>"
        # card, passing the OTP on to submit_auth through the banking tool server.
        bm = re.search(r"2FA-Code für (.+?) \(Auth-Request", rt)
        if bm and "Banking-Sync" in rt:
            source, code = bm.group(1).strip(), text.strip()
            try:
                from ..worker.mcp_client import mcp_session
                spec = {"name": "banking", "transport": "http",
                        "url": "http://banking-mcp:3010/mcp", "headers": {}}
                async with mcp_session("bot", servers=[spec], gateway_url="", gateway_token="") as mcp:
                    await mcp.call("banking__submit_auth", {"source": source, "code": code})
                await m.answer(f"✅ 2FA-Code an {source} weitergeleitet.")
            except Exception as exc:  # noqa: BLE001
                await m.answer(f"⚠ Weiterleitung an {source} fehlgeschlagen: {exc}")
            return

        # Without a ticket reference the answer is an assignment to the assistant, and to
        # EXACTLY this message. The quoted text therefore goes along as the reference, otherwise
        # the person would have to repeat in every answer what it was about.
        match = re.search(r"\[([A-Z][A-Z0-9]*-\d+)\]", rt)
        if not match:
            await _chat_task(m, reference=rt, text=text)
            return
        key = match.group(1)
        async with SessionLocal() as db:
            iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
            if iss is None:
                await _chat_task(m, reference=rt, text=text)
                return
            user = await _acting_user(db, m.chat.id)
            await apply_user_comment(db, iss, text, user.id if user else None, "Telegram")
        await m.answer(f"↳ Kommentar zu {key} gespeichert.")

    async def _chat_task(m: Message, reference: str = "", text: str | None = None) -> bool:
        """Hand plain text to the personal assistant. True means accepted.

        `bezug` is the text of the message that was answered. An answer means
        always EXACTLY this message: without the reference it would be just another line in the
        thread, and the assistant would have to guess what "do that" refers to.
        `text` overrides `m.text`, used for already transcribed voice messages, which from here
        on are treated EXACTLY like incoming text, with no special path.
        """
        text = (text if text is not None else (m.text or "")).strip()
        if not text or text.startswith("/"):
            return False
        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            await create_chat_task(db, user.id if user else None, text, str(m.chat.id),
                                   reference=reference)
        await m.answer("🤖 …")
        return True

    @dp.message(F.text)
    async def _assistant_chat(m: Message):
        # Plain text (no command, no ticket reply) means a chat with the assistant. Registered
        # AFTER commands and replies, so those take precedence.
        if not await _allowed(m.from_user.id):
            return
        await _chat_task(m)

    @dp.message(F.voice | F.audio | F.video_note)
    async def _voice_chat(m: Message):
        # A voice message (not a reply, `_reply` catches those) goes to the assistant like plain
        # text. Transcribed locally (faster-whisper), no cloud call.
        if not await _allowed(m.from_user.id):
            return
        text = await _voice_transcript(bot, m)
        if not text:
            return   # None never happens here (the filter only matches audio); "" means already refused
        await _chat_task(m, text=text)

    @dp.message()
    async def _unsupported(m: Message):
        # Everything without text or voice (photo, sticker, document) used to fall through every
        # handler and was discarded WITHOUT COMMENT, indistinguishable from being ignored.
        # unterscheiden. Lieber ehrlich absagen.
        if not m.from_user or not await _allowed(m.from_user.id):
            return
        await m.answer("🙉 Damit kann ich noch nichts anfangen — schick es mir als Text oder "
                       "Sprachnachricht.")

    @dp.callback_query()
    async def _cb(cq: CallbackQuery):
        if not await _allowed(cq.from_user.id):
            await cq.answer("Nicht erlaubt")
            return
        data = cq.data or ""
        async with SessionLocal() as db:
            if data.startswith("approve:") or data.startswith("reject:"):
                key = data.split(":", 1)[1]
                iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
                if iss and iss.agent_status == TicketAgentStatus.plan_review:
                    who = f"{cq.from_user.first_name or cq.from_user.id} (Telegram)"
                    if data.startswith("approve:"):
                        await set_ticket_status(db, iss, TicketAgentStatus.approved)
                        await add_system_comment(db, iss.id, f"✅ Plan freigegeben von {who}")
                    else:
                        iss.plan = None
                        await set_ticket_status(db, iss, None, board=False)
                        await add_system_comment(db, iss.id, f"✖ Plan abgelehnt von {who}")
                    iss.hold_reason = None
                    await db.commit()
                    await cq.answer("OK")
                    await _done(cq, "✅ Plan freigegeben" if data.startswith("approve:")
                                    else "✖ Plan abgelehnt")
                else:
                    await cq.answer("nicht mehr offen")
                    await _done(cq, "⏭ nicht mehr offen (anderswo entschieden)")
            elif data.startswith("accept:"):
                key = data.split(":", 1)[1]
                iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
                if iss and iss.agent_status in (TicketAgentStatus.to_test, TicketAgentStatus.testing):
                    await set_ticket_status(db, iss, TicketAgentStatus.done)
                    iss.resolved_at = _now()
                    iss.hold_reason = None
                    await db.commit()
                    from ..core.redis import enqueue_task
                    await enqueue_task({"kind": "accept", "task_id": f"accept-{iss.key}",
                                        "issue_id": iss.id, "project_id": iss.project_id})
                    await cq.answer("Abgenommen")
                    await _done(cq, "✅ Abgenommen")
                else:
                    await cq.answer("nicht mehr offen")
                    await _done(cq, "⏭ nicht mehr offen (anderswo entschieden)")
            elif data.startswith("perm:"):
                _, dec, rid = data.split(":", 2)
                pr = await db.get(PermRequest, int(rid))
                if pr and pr.status == "pending":
                    iss = await db.get(Issue, pr.issue_id)
                    if dec == "once":
                        db.add(PermGrant(issue_id=pr.issue_id, tool=pr.tool, resource=pr.resource))
                    elif dec == "always":
                        db.add(Permission(project_id=iss.project_id, tool=pr.tool, resource="*", action=PermAction.allow))
                    elif dec == "never":
                        db.add(Permission(project_id=iss.project_id, tool=pr.tool, resource="*", action=PermAction.deny))
                    pr.status = "decided"
                    pr.decision = dec
                    pr.decided_at = _now()
                    if iss and iss.agent_status == TicketAgentStatus.hold and dec != "never":
                        await set_ticket_status(db, iss, TicketAgentStatus.approved)
                        iss.hold_reason = None
                        iss.continuation_count += 1
                    await db.commit()
                    await cq.answer(f"Berechtigung: {dec}")
                    await _done(cq, f"🔑 Berechtigung: {_DEC_TEXT.get(dec, dec)}")
                else:
                    await cq.answer("schon entschieden")
                    await _done(cq, "⏭ schon entschieden")
            elif data.startswith("atask:"):
                _, action, sid = data.split(":", 2)
                t = await db.get(AssistantTask, int(sid))
                if t is None:
                    await cq.answer("Not found")
                    await _done(cq, "⏭ Aufgabe nicht mehr vorhanden")
                elif t.status not in ("new", "error"):
                    await cq.answer(f"schon erledigt ({t.status})")
                    await _done(cq, f"⏭ schon erledigt ({t.status})")
                elif action == "reject":
                    await reject_assistant_task(db, t)
                    await cq.answer("Verworfen")
                    await _done(cq, "❌ Verworfen")
                else:
                    scope = {"sender": "sender", "category": "category"}.get(action, "once")
                    # Quick approval by chat is redacted (safe); unredacted is handled by the web inbox.
                    await approve_assistant_task(db, t, scope=scope, redaction="redacted")
                    await cq.answer("Freigegeben" + ("" if scope == "once" else " + gemerkt"))
                    await _done(cq, "✅ Freigegeben" + {
                        "sender": " · Absender künftig automatisch",
                        "category": " · Kategorie künftig automatisch"}.get(scope, ""))
            elif data.startswith("spam:"):
                _, answer, vid = data.split(":", 2)
                v = await db.get(SpamVerdict, int(vid))
                if v is None:
                    await cq.answer("Not found")
                    await _done(cq, "⏭ Urteil nicht mehr vorhanden")
                elif v.status not in ("pending", "spam", "ham"):
                    await cq.answer(f"schon erledigt ({v.status})")
                    await _done(cq, f"⏭ schon erledigt ({v.status})")
                else:
                    # A row already decided may be decided again: the mistake often only shows
                    # when the mail is missing. `entscheiden` counts the old assessment back out
                    # of the memory.
                    is_spam = answer == "yes"
                    result = await decide(db, v, is_spam, decided_by="telegram")
                    await cq.answer("Als Spam markiert" if is_spam else "Als erwünscht gemerkt")
                    header = "✅ Spam · verschoben" if is_spam else "🚫 Kein Spam · Absender gemerkt"
                    await _done(cq, f"{header}\n{result}")
            elif data.startswith("spamundo:"):
                _, vid = data.split(":", 1)
                v = await db.get(SpamVerdict, int(vid))
                if v is None:
                    await cq.answer("Not found")
                    await _done(cq, "⏭ Urteil nicht mehr vorhanden")
                else:
                    result = await reclaim(db, v)
                    await cq.answer("Zurückgeholt")
                    await _done(cq, f"↩️ Zurückgeholt · Absender gemerkt\n{result}")

            elif data.startswith("spamall:"):
                _, answer, batch = data.split(":", 2)
                if answer == "einzeln":
                    cases = (await db.execute(select(SpamVerdict).where(
                        SpamVerdict.digest_batch == batch,
                        SpamVerdict.status == "pending").order_by(SpamVerdict.id))).scalars().all()
                    if not cases:
                        await cq.answer("nichts mehr offen")
                        await _done(cq, "⏭ nichts mehr offen")
                    else:
                        # Every case gets a card of its own, so the collective card itself is
                        # handled.
                        for v in cases:
                            title, text = karte(v)
                            await bot.send_message(
                                cq.message.chat.id,
                                f"<b>{safe(title)}</b>\n{safe(text)}",
                                parse_mode="HTML", reply_markup=_spam_kb(v.id))
                        await cq.answer(f"{len(cases)} einzeln")
                        await _done(cq, f"👉 {len(cases)} Fälle einzeln zugestellt")
                else:
                    is_spam = answer == "yes"
                    count, error = await decide_batch(db, batch, is_spam,
                                                            decided_by="telegram")
                    await cq.answer(f"{count} entschieden")
                    header = ("✅ alle als Spam verschoben" if is_spam
                            else "🚫 alle als erwünscht gemerkt")
                    await _done(cq, f"{header} ({count})"
                                    + (f" · {error} nicht verschiebbar" if error else ""))
            elif data.startswith("aperm:"):
                _, dec, sid = data.split(":", 2)
                t = await db.get(AssistantTask, int(sid))
                if t is None or t.status != "awaiting":
                    await cq.answer("schon entschieden")
                    await _done(cq, "⏭ schon entschieden")
                else:
                    await apply_perm_decision(db, t, dec)
                    await cq.answer(f"Freigabe: {dec}")
                    await _done(cq, f"🔑 Freigabe: {_DEC_TEXT.get(dec, dec)}")
        await cq.answer()

    log.info("Traccoon bot started (allowed=%s)", ALLOWED or "alle")
    asyncio.create_task(notifier())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
