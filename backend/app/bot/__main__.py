"""Traccoon Telegram-Bot (aiogram v3). Teilt das Backend-Image (python -m app.bot).

Notifier-Poll (Notification → Telegram, mit Medium falls `media_path` gesetzt),
Reply→Kommentar (geteilte apply_user_comment),
Sprachnachrichten (voice/audio/video_note → lokale Transkription → derselbe Weg wie Text),
Inline-Buttons (approve/reject/accept/perm), Commands /tasks /comment.
No-op (stabiler Sleep) wenn kein TELEGRAM_BOT_TOKEN gesetzt.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re

from sqlalchemy import select

from ..db import SessionLocal
from ..models.assistant import AssistantTask
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
from ..worker.assistant_gate import apply_perm_decision
from .mdtg import clip, safe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("traccoon.bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "")

# Sprachnachrichten: lokaler faster-whisper-Container (kein Cloud-Aufruf, kein Audio verlässt
# das Haus — Vorgabe des Nutzers). `/asr` ist der native Endpunkt von
# onerahmet/openai-whisper-asr-webservice mit ASR_ENGINE=faster_whisper.
WHISPER_URL = os.getenv("WHISPER_URL", "http://whisper:9000")
# 10 Minuten Standardgrenze — länger ist am Handy ungewöhnlich und würde den CPU-Container
# lange blockieren. Bewusst konfigurierbar statt fest, falls sich das als zu eng erweist.
VOICE_MAX_SECONDS = int(os.getenv("TELEGRAM_VOICE_MAX_SECONDS", "600"))
# Fallback-Obergrenze über die Dateigröße, falls Telegram kein `duration` mitliefert (kommt
# bei manchen `audio`-Uploads ohne Metadaten vor) — ohne sie wäre die Längenprüfung dann
# wirkungslos. Grober Anhalt: OGG/Opus-Sprachnachrichten liegen bei ~1 MB je Minute.
# WICHTIG: die Bot-API (ohne eigenen lokalen Bot-API-Server) lehnt `getFile`/den Download
# JEDER Datei über 20 MB ab — ein Default darüber (z. B. 25 MB) würde Dateien zwischen 20
# und 25 MB die Größenprüfung passieren lassen, die dann erst beim Download mit einer
# technischen Exception scheitern und die irreführende „konnte nicht geladen werden"-Meldung
# statt der beabsichtigten „zu groß"-Meldung auslösen. Deshalb 19 MB Default (Sicherheits-
# abstand zum harten 20-MB-Limit).
VOICE_MAX_BYTES = int(os.getenv("TELEGRAM_VOICE_MAX_BYTES", str(19 * 1024 * 1024)))


# Whitelist bekannter Audio-Container für `audio`-Uploads (mime_type → Dateiendung).
# `mime_type` ist ein vom SENDENDEN CLIENT frei befülltes Metadatum aus der Telegram-Nachricht
# — keine verifizierte serverseitige Eigenschaft. Würde der Rohwert ungeprüft als HTTP-
# Content-Type des Multipart-Teils an den Whisper-Container weitergereicht, könnte ein
# präparierter `mime_type` (Kontroll-/Sonderzeichen, beliebiger String) dort landen. Deshalb
# nur bekannte, harmlose Werte durchlassen — alles andere fällt auf einen sicheren Default.
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


def _upload_name_typ(medienart: str, mime_type: str | None) -> tuple[str, str]:
    """Dateiname+Content-Type passend zum tatsächlichen Medientyp — NICHT pauschal
    `audio.ogg`/`application/octet-stream`: `voice` ist tatsächlich OGG/Opus, aber
    `audio`-Uploads sind häufig MP3/M4A/WAV und `video_note` ist ein MP4-Container
    (Video+Audio-Spur). Erkennt ffmpeg im Whisper-Image das Format anhand einer
    falschen Erweiterung/eines falschen Content-Type nicht, schlägt die Transkription
    fehl oder liefert Müll — und der Nutzer bekommt fälschlich „keine Sprache erkannt"
    statt der wahren Ursache.

    `mime_type` kommt UNGEPRÜFT von Telegram (letztlich vom sendenden Client) — deshalb
    gegen `_AUDIO_MIME_WHITELIST` prüfen statt den Rohwert direkt als HTTP-Content-Type
    zu übernehmen. Unbekannter/verdächtiger Wert → sicherer Default statt Weiterreichen.
    """
    if medienart == "video_note":
        return "video_note.mp4", "video/mp4"
    if medienart == "audio":
        mime = (mime_type or "").strip().lower()
        endung = _AUDIO_MIME_WHITELIST.get(mime)
        if endung is None:
            # Nicht gelistet (unbekanntes Format ODER manipulierter Wert) — sicherer
            # Default statt Rohwert ungeprüft in den Multipart-Header zu übernehmen.
            return "audio.mp3", "audio/mpeg"
        return f"audio.{endung}", mime
    return "voice.ogg", "audio/ogg"


async def _transkribieren(audio: bytes, medienart: str = "voice",
                           mime_type: str | None = None) -> str:
    """Sprachnachricht lokal transkribieren. Erst Deutsch (häufigster Fall), bei LEEREM
    Ergebnis (nicht bei technischem Fehler) ein zweiter Versuch ohne Sprachangabe
    (Auto-Erkennung) — ein 4xx/5xx vom Container oder ein Verbindungsfehler bricht sofort ab,
    denn eine erneute komplette Übertragung derselben Datei würde die Verarbeitungszeit bei
    einer langen Nachricht verdoppeln, ohne dass sich am Fehler etwas ändert.
    Leer nach dem ersten Versuch → zweiter Versuch; leer nach beiden → leerer String, kein
    Fehler. Ein wirklicher Fehler wird weitergereicht, damit der Aufrufer ehrlich absagen
    kann statt stumm zu bleiben.

    Timeout an `VOICE_MAX_SECONDS` gekoppelt statt fest: eine erlaubte 10-Minuten-Nachricht
    braucht auf CPU (Modell "small") durchaus mehrere Minuten Transkriptionszeit — ein
    fixer 120s-Timeout würde genau die Nachrichten abbrechen, die der Längen-Check erlaubt.
    Faktor 1.0 der Nachrichtenlänge plus 60s Sockel für Modell-Ladezeit/Overhead, mindestens
    120s für kurze Nachrichten.
    """
    import httpx
    if not WHISPER_URL:
        raise RuntimeError("kein WHISPER_URL konfiguriert (lokaler Whisper-Container fehlt)")
    timeout = max(120.0, VOICE_MAX_SECONDS + 60.0)
    dateiname, content_type = _upload_name_typ(medienart, mime_type)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for sprache in ("de", None):
            params = {"output": "json"}
            if sprache:
                params["language"] = sprache
            # Ein technischer Fehler (nicht erreichbar, abgelehntes Format, 4xx/5xx) wird
            # NICHT abgefangen, sondern reicht bis zum Aufrufer durch — ein zweiter Versuch
            # würde denselben Fehler nur wiederholen und zusätzlich Zeit kosten.
            resp = await client.post(f"{WHISPER_URL}/asr", params=params,
                                     files={"audio_file": (dateiname, audio, content_type)})
            resp.raise_for_status()
            text = (resp.json().get("text") or "").strip()
            if text:
                return text
    return ""


# Entscheidungen der Freigabe-Knöpfe im Klartext, für den Vermerk an der Nachricht.
_DEC_TEXT = {"once": "einmal", "always": "immer", "never": "nie"}


async def _erledigt(cq: CallbackQuery, vermerk: str) -> None:
    """Tastatur entfernen und den Ausgang an die Frage schreiben.

    Damit sieht man im Verlauf sofort, was noch offen ist: beantwortete Fragen tragen
    keine Knöpfe mehr, sondern eine Zeile mit Entscheidung und Zeitpunkt. Auch bei
    „schon erledigt" (anderswo entschieden) müssen die Knöpfe weg — sonst laden sie
    weiter zum Drücken ein.
    """
    msg = cq.message
    if msg is None:
        return
    zeile = f"<i>{safe(vermerk)} · {_now().strftime('%d.%m. %H:%M')}</i>"
    try:
        # html_text erhält die Formatierung der Ursprungsnachricht (Fettung, Zeilen).
        await msg.edit_text(f"{msg.html_text}\n\n{zeile}", parse_mode="HTML")
        return
    except Exception:  # noqa: BLE001
        # Zu alt zum Bearbeiten, ohne Text (Foto) oder unverändert — dann wenigstens
        # die Knöpfe abräumen, das ist der eigentliche Zweck.
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            log.warning("Konnte Tastatur an Nachricht %s nicht entfernen", msg.message_id)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# --- Medienausgang ---------------------------------------------------------------------
# Dieser Prozess ist der EINZIGE Weg nach Telegram: dem backend-Container fehlt
# `TELEGRAM_BOT_TOKEN` vollständig (er hat nur `TELEGRAM_OWNER_CHAT`), und ein Sendeaufruf
# an Telegram steht im ganzen Backend an genau einer Stelle — der hier. Wer eine Datei
# mitschicken will, legt sie an einen für backend UND telegram-bot sichtbaren Pfad und
# schreibt ihn in `Notification.media_path`. Ein zweiter Ausgang wäre die Sorte Doppelung,
# gegen die dieses Repo sonst durchgehend argumentiert.

def _teilbloecke_ende(roh: bytes, i: int) -> int:
    """Ende einer GIF-Teilblockkette (Längenbyte, Daten, …, 0x00)."""
    while i < len(roh) and roh[i]:
        i += roh[i] + 1
    return i + 1


def _gif_masse(roh: bytes) -> dict[str, int]:
    """Breite/Höhe/Dauer aus dem GIF selbst — leeres Dict, wenn es keins ist.

    Warum überhaupt messen statt feste Werte einzutragen: dieser Ausgang kennt nur „eine
    Datei", nicht ihren Absender. Feste Maße wären eine Behauptung über einen Inhalt, den
    der Notifier nicht kennen darf — beim ersten anderen Format stünde eine Lüge im Code.
    Warum es sich lohnt: Telegram dimensioniert die Blase aus genau diesen Angaben, BEVOR
    die Datei geladen ist. Ohne sie springt das Layout beim Eintreffen.
    Unlesbar/kein GIF → nichts behaupten, Telegram misst dann selbst nach dem Laden.
    """
    try:
        if not roh.startswith(b"GIF") or len(roh) < 13:
            return {}
        breite = int.from_bytes(roh[6:8], "little")
        hoehe = int.from_bytes(roh[8:10], "little")
        i = 13
        if roh[10] & 0x80:                       # globale Farbtabelle überspringen
            i += 3 * (2 ** ((roh[10] & 7) + 1))
        hundertstel = 0
        while i < len(roh):
            marke = roh[i]
            if marke == 0x21:                    # Erweiterung
                label = roh[i + 1]
                i += 2
                if label == 0xF9 and i + 4 <= len(roh):
                    # Bildsteuerung: Blockgröße, Kennzeichen, Verzögerung (1/100 s).
                    hundertstel += int.from_bytes(roh[i + 2:i + 4], "little")
                i = _teilbloecke_ende(roh, i)
            elif marke == 0x2C:                  # Bildbeschreibung
                kennzeichen = roh[i + 9]
                i += 10
                if kennzeichen & 0x80:           # lokale Farbtabelle
                    i += 3 * (2 ** ((kennzeichen & 7) + 1))
                i += 1                           # LZW-Mindestcodegröße
                i = _teilbloecke_ende(roh, i)
            else:                                # 0x3B (Ende) oder Unerwartetes
                break
        masse = {"width": breite, "height": hoehe}
        if hundertstel:
            # Telegram will ganze Sekunden; ein Film unter einer Sekunde ist trotzdem einer.
            masse["duration"] = max(1, round(hundertstel / 100))
        return masse
    except Exception:  # noqa: BLE001
        return {}


async def _zustellen(bot, n, text: str, markup) -> None:
    """Eine Notification zustellen — mit Medium, wenn eines daliegt, sonst als Text.

    Drei Festlegungen, die hier zusammenkommen:
    * `send_animation` und nicht `send_video` (das erzwingt Ton-Container-Semantik) und
      nicht `send_photo` (Standbild). APNG scheidet ohnehin aus: Telegram zeigt es nicht
      als Animation, sondern nur als Sticker.
    * Fehlende Datei → stiller Rückfall auf Text. Ein Film, der nicht da ist, darf keine
      Nachricht verschlucken — die Nachricht ist der Zweck, das Medium die Zugabe.
      (Häufigste Ursache: der `./data/film`-Mount fehlt in DIESEM Dienst.)
    * `notified_at` wird IMMER gesetzt, auch im Fehlerfall. Es ist die einzige Bremse des
      Pollers; ohne sie versucht er dieselbe Zeile alle drei Sekunden endlos erneut.
    Die Tastatur geht auf beiden Wegen unverändert mit — die Knöpfe (Freigeben/Ablehnen/
    Abnehmen/Berechtigungen) sind am Medium genauso gültig wie am Text.
    """
    try:
        pfad = n.media_path or ""
        if pfad and os.path.isfile(pfad):
            from aiogram.types import BufferedInputFile
            with open(pfad, "rb") as fh:
                roh = fh.read()
            datei = BufferedInputFile(roh, filename=os.path.basename(pfad))
            # Telegram kappt Bildunterschriften bei 1024 Zeichen — ungekürzt wird die
            # Nachricht abgewiesen statt bloß beschnitten.
            beschriftung = text[:1024]
            art = (n.media_kind or "").strip() or "animation"
            if art == "photo":
                await bot.send_photo(int(n.chat_id), photo=datei, caption=beschriftung,
                                     parse_mode="HTML", reply_markup=markup)
            elif art == "document":
                await bot.send_document(int(n.chat_id), document=datei, caption=beschriftung,
                                        parse_mode="HTML", reply_markup=markup)
            else:
                await bot.send_animation(int(n.chat_id), animation=datei, caption=beschriftung,
                                         parse_mode="HTML", reply_markup=markup,
                                         **_gif_masse(roh))
        else:
            if pfad:
                log.warning("Medium %s nicht lesbar — Notification %s geht als Text", pfad, n.id)
            await bot.send_message(int(n.chat_id), text, parse_mode="HTML", reply_markup=markup)
    except Exception:  # noqa: BLE001
        log.exception("Send an %s fehlgeschlagen", n.chat_id)
    finally:
        n.notified_at = _now()


async def _acting_user(db, chat_id: str) -> User | None:
    u = (await db.execute(select(User).where(User.telegram_chat_id == str(chat_id)))).scalar_one_or_none()
    if u:
        return u
    return (await db.execute(select(User).where(User.global_role == GlobalRole.admin).order_by(User.id))).scalars().first()


async def _voice_transkript(bot, m) -> str | None:
    """Sprachnachricht (voice/audio/video_note) in Text auflösen — inkl. sichtbarer
    Rückmeldung, damit Fehlhörungen sofort auffallen (Lehre aus 2026-07-29: lieber
    ehrlich absagen als still bleiben).

    Rückgabe: None = `m` ist gar keine Sprachnachricht (Aufrufer macht normal weiter).
    "" = Sprachnachricht, aber nicht verarbeitbar — diese Funktion hat SELBST schon die
    Absage mit Grund geschickt, der Aufrufer bricht einfach ab. Nichtleerer String =
    Transkript, exakt wie eingehender Text weiterzureichen.

    `bot` als expliziter Parameter (statt Closure-Zugriff aus `run_bot()`) — sonst wäre
    diese Funktion nur mit einem echten aiogram-Bot testbar, genau das Gegenteil vom
    Muster `_zustellen`/`_gif_masse`.
    """
    media = m.voice or m.audio or m.video_note
    if media is None:
        return None
    dauer = getattr(media, "duration", 0) or 0
    groesse = getattr(media, "file_size", 0) or 0
    # Beide Signale sind UNABHÄNGIG voneinander zu prüfen, nicht als Alternative: eine
    # (ggf. gefälschte oder schlicht falsche) kurze `duration` bei tatsächlich sehr
    # großer `file_size` darf die Größenprüfung nicht überspringen — sonst würde genau
    # das Speicher-/CPU-Risiko eintreten, das diese Prüfung verhindern soll.
    if dauer and dauer > VOICE_MAX_SECONDS:
        await m.answer(f"🙉 Sprachnachricht zu lang ({dauer // 60} Min., Grenze "
                       f"{VOICE_MAX_SECONDS // 60} Min.) — bitte kürzer aufnehmen oder "
                       f"als Text schicken.")
        return ""
    if groesse and groesse > VOICE_MAX_BYTES:
        await m.answer(f"🙉 Datei zu groß ({groesse // (1024 * 1024)} MB, Grenze "
                       f"{VOICE_MAX_BYTES // (1024 * 1024)} MB) — bitte kürzer "
                       f"aufnehmen oder als Text schicken.")
        return ""
    if not dauer and not groesse:
        # Weder `duration` noch `file_size` verwertbar — OHNE eine der beiden
        # Prüfungen wäre eine beliebig große Datei ungebremst komplett in den
        # Speicher geladen und an Whisper weitergereicht (Speicher-/CPU-Risiko).
        # Lieber ehrlich absagen statt ungeprüft zu laden.
        await m.answer("🙉 Länge/Größe der Datei nicht bestimmbar — bitte als Text "
                       "schicken oder als reguläre Sprachnachricht erneut aufnehmen.")
        return ""
    try:
        datei = await bot.get_file(media.file_id)
        puffer = await bot.download_file(datei.file_path)
        roh = puffer.read() if hasattr(puffer, "read") else bytes(puffer)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sprachnachricht %s nicht ladbar: %s", media.file_id, exc)
        await m.answer("🙉 Sprachnachricht konnte nicht geladen werden — bitte als Text schicken.")
        return ""
    medienart = "voice" if m.voice else ("video_note" if m.video_note else "audio")
    mime_type = getattr(media, "mime_type", None)
    try:
        text = await _transkribieren(roh, medienart=medienart, mime_type=mime_type)
    except Exception as exc:  # noqa: BLE001
        log.warning("Transkription fehlgeschlagen: %s", exc)
        await m.answer(f"🙉 Transkription nicht möglich ({exc}) — bitte als Text schicken.")
        return ""
    if not text:
        await m.answer("🙉 Ich konnte darin keine Sprache erkennen — bitte als Text schicken.")
        return ""
    # Roh (ohne `safe()`/HTML-Escaping) senden: `safe()` escaped & wandelt Markdown-artige
    # Sequenzen in <b>/<i>/<code>-Tags um, aber dieser Bot läuft OHNE
    # `parse_mode="HTML"` (weder hier per Aufruf noch als Bot-Default) — ein HTML-escapter
    # String käme dann ungeparst an: `&`/`<`/`>` erschienen als literale Entities und
    # umgewandelte Tags als sichtbarer `<b>…</b>`-Text statt Fettung. Hier ist ohnehin keine
    # Formatierung gewünscht, nur die reine, auf Telegram-Länge gekappte Transkription.
    await m.answer(f"🎙 verstanden: {clip(text)}")
    return text


async def run_bot() -> None:
    if not TOKEN:
        log.warning("Kein TELEGRAM_BOT_TOKEN — Bot im Ruhemodus.")
        while True:
            await asyncio.sleep(3600)

    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

    bot = Bot(TOKEN)
    dp = Dispatcher()

    async def _allowed(uid: int) -> bool:
        # Erlaubt: Env-Bootstrap (TELEGRAM_ALLOWED_IDS) ODER jeder User, der seine
        # Chat-ID in der WebUI hinterlegt hat. DB-Lookup pro Aufruf → neue User
        # greifen sofort, ohne Bot-Neustart / Env-Edit.
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
        # Freigabe eines Assistent-Eingangs. Schnellfreigabe ist geschwärzt (sicher);
        # ungeschwärzt/feineres regelt die Web-Inbox.
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Freigeben", callback_data=f"atask:approve:{tid}"),
             InlineKeyboardButton(text="❌ Verwerfen", callback_data=f"atask:reject:{tid}")],
            [InlineKeyboardButton(text="♾️ Immer Absender", callback_data=f"atask:sender:{tid}"),
             InlineKeyboardButton(text="♾️ Immer Kategorie", callback_data=f"atask:category:{tid}")]])

    def _aperm_kb(tid: int) -> InlineKeyboardMarkup:
        # Tool-Freigabe des Assistenten (einmal|immer|nie).
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
                        else:
                            markup = _kb_for(n.kind, issue_key, req_id)
                        # Text oder Medium entscheidet `_zustellen` — und setzt in jedem
                        # Fall `notified_at` (sonst endlos retry).
                        await _zustellen(bot, n, text, markup)
                    await db.commit()
            except Exception:  # noqa: BLE001
                log.exception("notifier-Fehler")
            await asyncio.sleep(3)

    @dp.message(Command("start"))
    async def _start(m: Message):
        await m.answer("🦝 Traccoon-Bot. /tasks · /comment &lt;KEY&gt; &lt;Text&gt; · /uniwar &lt;Text&gt;")

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

    @dp.message(Command("uniwar"))
    async def _uniwar_chat(m: Message):
        # Chat mit dem UniWar-Operator statt mit dem persönlichen Assistenten. Gleicher Weg wie
        # `_assistant_chat`, nur mit gesetztem meta.agent — `_handle_assistant_task` löst den
        # Agenten daraus auf (sonst fällt es auf 'assistent' zurück).
        if not await _allowed(m.from_user.id):
            return
        text = (m.text or "").split(maxsplit=1)
        text = text[1].strip() if len(text) > 1 else ""
        if not text:
            await m.answer("Nutzung: /uniwar &lt;Frage oder Auftrag&gt;")
            return
        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            await create_chat_task(db, user.id if user else None, text, str(m.chat.id),
                                   agent="uniwar-operator")
        await m.answer("🛰 …")

    @dp.message(F.reply_to_message)
    async def _reply(m: Message):
        if not await _allowed(m.from_user.id):
            return
        rt = m.reply_to_message.text or m.reply_to_message.html_text or ""

        # Antwort per Sprachnachricht: gleicher Zitat-Bezug wie bei Text, nur transkribiert.
        text = m.text
        if text is None:
            gehoert = await _voice_transkript(bot, m)
            if gehoert is None:
                await m.answer("🙉 Damit kann ich noch nichts anfangen — schick es mir als "
                               "Text oder Sprachnachricht.")
                return
            if not gehoert:
                return   # _voice_transkript hat die Absage schon geschickt
            text = gehoert

        # Banking-2FA: Antwort auf die „Banking-Sync braucht einen 2FA-Code für <source>"-Karte
        # → OTP über das banking-MCP an submit_auth weiterreichen (ersetzt den Hermes-Relay).
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

        # Ohne Ticket-Bezug ist die Antwort ein Auftrag an den Assistenten — und zwar zu
        # GENAU dieser Nachricht. Der zitierte Text geht deshalb als Bezug mit, sonst müsste
        # der Mensch in jeder Antwort wiederholen, worum es ging.
        match = re.search(r"\[([A-Z][A-Z0-9]*-\d+)\]", rt)
        if not match:
            await _chat_auftrag(m, bezug=rt, text=text)
            return
        key = match.group(1)
        async with SessionLocal() as db:
            iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
            if iss is None:
                await _chat_auftrag(m, bezug=rt, text=text)
                return
            user = await _acting_user(db, m.chat.id)
            await apply_user_comment(db, iss, text, user.id if user else None, "Telegram")
        await m.answer(f"↳ Kommentar zu {key} gespeichert.")

    async def _chat_auftrag(m: Message, bezug: str = "", text: str | None = None) -> bool:
        """Klartext an den persönlichen Assistenten übergeben. True = angenommen.

        `bezug` ist der Text der Nachricht, auf die geantwortet wurde. Eine Antwort meint
        immer GENAU diese Nachricht — ohne den Bezug wäre sie nur eine weitere Zeile im
        Gesprächsfaden, und der Assistent müsste raten, worauf sich „mach das" bezieht.
        `text` überschreibt `m.text` — genutzt für bereits transkribierte Sprachnachrichten,
        die ab hier GENAUSO behandelt werden wie eingehender Text, kein Sonderweg.
        """
        text = (text if text is not None else (m.text or "")).strip()
        if not text or text.startswith("/"):
            return False
        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            await create_chat_task(db, user.id if user else None, text, str(m.chat.id),
                                   bezug=bezug)
        await m.answer("🤖 …")
        return True

    @dp.message(F.text)
    async def _assistant_chat(m: Message):
        # Klartext (kein Command, keine Ticket-Antwort) → Chat mit dem Assistenten.
        # Registriert NACH Commands/Reply → die haben Vorrang.
        if not await _allowed(m.from_user.id):
            return
        await _chat_auftrag(m)

    @dp.message(F.voice | F.audio | F.video_note)
    async def _voice_chat(m: Message):
        # Sprachnachricht (kein Reply — das fängt `_reply` schon ab) → wie Klartext an den
        # Assistenten. Lokal transkribiert (faster-whisper), kein Cloud-Aufruf.
        if not await _allowed(m.from_user.id):
            return
        text = await _voice_transkript(bot, m)
        if not text:
            return   # None kommt hier nie vor (Filter greift nur bei Audio); "" = schon abgesagt
        await _chat_auftrag(m, text=text)

    @dp.message()
    async def _unsupported(m: Message):
        # Alles ohne Text/Sprachnachricht (Foto, Sticker, Dokument) fiel bisher durch alle
        # Handler und wurde KOMMENTARLOS verworfen — von außen nicht von „ignoriert" zu
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
                    await _erledigt(cq, "✅ Plan freigegeben" if data.startswith("approve:")
                                    else "✖ Plan abgelehnt")
                else:
                    await cq.answer("nicht mehr offen")
                    await _erledigt(cq, "⏭ nicht mehr offen (anderswo entschieden)")
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
                    await _erledigt(cq, "✅ Abgenommen")
                else:
                    await cq.answer("nicht mehr offen")
                    await _erledigt(cq, "⏭ nicht mehr offen (anderswo entschieden)")
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
                    await _erledigt(cq, f"🔑 Berechtigung: {_DEC_TEXT.get(dec, dec)}")
                else:
                    await cq.answer("schon entschieden")
                    await _erledigt(cq, "⏭ schon entschieden")
            elif data.startswith("atask:"):
                _, action, sid = data.split(":", 2)
                t = await db.get(AssistantTask, int(sid))
                if t is None:
                    await cq.answer("Nicht gefunden")
                    await _erledigt(cq, "⏭ Aufgabe nicht mehr vorhanden")
                elif t.status not in ("new", "error"):
                    await cq.answer(f"schon erledigt ({t.status})")
                    await _erledigt(cq, f"⏭ schon erledigt ({t.status})")
                elif action == "reject":
                    await reject_assistant_task(db, t)
                    await cq.answer("Verworfen")
                    await _erledigt(cq, "❌ Verworfen")
                else:
                    scope = {"sender": "sender", "category": "category"}.get(action, "once")
                    # Schnellfreigabe per Telegram ist geschwärzt (sicher); ungeschwärzt regelt die Web-Inbox.
                    await approve_assistant_task(db, t, scope=scope, redaction="redacted")
                    await cq.answer("Freigegeben" + ("" if scope == "once" else " + gemerkt"))
                    await _erledigt(cq, "✅ Freigegeben" + {
                        "sender": " · Absender künftig automatisch",
                        "category": " · Kategorie künftig automatisch"}.get(scope, ""))
            elif data.startswith("aperm:"):
                _, dec, sid = data.split(":", 2)
                t = await db.get(AssistantTask, int(sid))
                if t is None or t.status != "awaiting":
                    await cq.answer("schon entschieden")
                    await _erledigt(cq, "⏭ schon entschieden")
                else:
                    await apply_perm_decision(db, t, dec)
                    await cq.answer(f"Freigabe: {dec}")
                    await _erledigt(cq, f"🔑 Freigabe: {_DEC_TEXT.get(dec, dec)}")
        await cq.answer()

    log.info("Traccoon-Bot gestartet (allowed=%s)", ALLOWED or "alle")
    asyncio.create_task(notifier())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
