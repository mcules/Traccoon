"""Benachrichtigungen: die Glocke immer, der Weg nach draußen nach Wahl der Person.

Bisher gab es genau einen Weg hinaus — Telegram, falls eine Chat-ID hinterlegt war. Wer
eine Benachrichtigung auslöst, weiß aber selten, ob der Empfänger Telegram überhaupt
benutzt; und in einem Ablauf steht der Empfänger oft erst zur Laufzeit fest. Deshalb
entscheidet die **Person**, auf welchem Weg sie erreicht wird (`users.notify_default`),
und der Absender darf einen Weg vorgeben, muss aber nicht.

Die Glocke bleibt unabhängig davon: jede Benachrichtigung ist auch eine Zeile in der
Oberfläche. Der Weg entscheidet nur, was zusätzlich hinausgeht.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.notification import Notification
from ..models.ticket import Issue
from ..models.user import User

OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "")

log = logging.getLogger("traccoon.notify")

KANAELE = ("telegram", "email")


def _mit_zone(ts: dt.datetime) -> dt.datetime:
    """Zeitstempel ohne Zone als UTC lesen — SQLite gibt sie nackt zurück."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=dt.timezone.utc)


def kanal_adresse(user: User | None, kanal: str) -> str:
    """Womit dieser Weg bei dieser Person erreichbar ist — leer, wenn gar nicht."""
    if user is None:
        return OWNER_CHAT if kanal == "telegram" else ""
    if kanal == "telegram":
        return user.telegram_chat_id or ""
    if kanal == "email":
        return (user.notify_email or user.email or "").strip()
    return ""


def waehle_kanal(user: User | None, gewuenscht: str = "") -> str:
    """Welcher Weg tatsächlich genommen wird.

    Vorgabe des Absenders schlägt Standard der Person; ist der gewählte Weg bei dieser
    Person nicht hinterlegt, wird der andere genommen, statt die Nachricht still fallen
    zu lassen. Eine Benachrichtigung, die niemanden erreicht, ist der schlechteste
    Ausgang — schlechter als eine auf dem zweitliebsten Weg.
    """
    reihenfolge = [k for k in (gewuenscht, (user.notify_default if user else ""), "telegram")
                   if k in KANAELE]
    reihenfolge += [k for k in KANAELE if k not in reihenfolge]
    for kanal in reihenfolge:
        if kanal_adresse(user, kanal):
            return kanal
    return reihenfolge[0]


async def zustellen(db: AsyncSession, *, user: User | None, kind: str, title: str,
                    body: str = "", kanal: str = "", project_id: int | None = None,
                    issue_id: int | None = None,
                    drossel_key: str = "", drossel_minuten: float = 0) -> dict:
    """Eine Benachrichtigung anlegen und auf dem passenden Weg hinausschicken.

    Telegram übernimmt wie bisher der Bot (er ist der einzige Prozess mit dem Bot-Token);
    hier wird dafür nur die Chat-ID gesetzt. E-Mail geht sofort raus — dafür braucht es
    keinen zweiten Prozess, und `notified_at` sagt der Glocke, dass draußen nichts mehr
    offen ist.

    Mit `drossel_key` und `drossel_minuten` wird dieselbe Nachricht innerhalb des Fensters
    unterdrückt — **vollständig**, auch die Glocke. Sie nur dort abzulegen hieße, den Lärm
    eine Etage tiefer zu schieben: 120 gleichlautende Zeilen machen eine Liste mit
    Ungelesen-Zähler genauso unbrauchbar wie 120 Telegramme. Nachvollziehbar bleibt es
    trotzdem — der Schritt im Ablauf protokolliert, dass gedrosselt wurde.
    """
    if drossel_key and drossel_minuten > 0:
        grenze = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=drossel_minuten)
        letzte = (await db.execute(
            select(Notification.created_at)
            .where(Notification.drossel_key == drossel_key,
                   # Nach Empfänger getrennt: zwei Menschen mit gleichem Schlüssel dürfen
                   # sich nicht gegenseitig stummschalten.
                   Notification.user_id == (user.id if user else None),
                   Notification.created_at >= grenze)
            .order_by(Notification.created_at.desc()).limit(1))).scalars().first()
        if letzte is not None:
            wieder = _mit_zone(letzte) + dt.timedelta(minutes=drossel_minuten)
            log.info("gedrosselt: %s (wieder ab %s)", drossel_key, wieder.isoformat())
            return {"kanal": "gedrosselt", "unterdrueckt": True, "drossel_key": drossel_key,
                    "wieder_ab": wieder.isoformat()}

    gewaehlt = waehle_kanal(user, kanal)
    ziel = kanal_adresse(user, gewaehlt)
    n = Notification(user_id=(user.id if user else None), project_id=project_id,
                     issue_id=issue_id, kind=kind, title=title[:500], body=(body or "")[:4000],
                     drossel_key=(drossel_key or None),
                     chat_id=(ziel or OWNER_CHAT or None) if gewaehlt == "telegram" else None)
    db.add(n)

    if gewaehlt == "email":
        if not ziel:
            log.warning("Keine E-Mail-Adresse für Nutzer %s — nur Glocke",
                        user.id if user else None)
            return {"kanal": "bell", "grund": "keine Adresse"}
        from . import mail
        ok = await mail.send_mail(db, ziel, title[:200] or "Traccoon",
                                  html_body=_html(title, body), text_body=body or title)
        if ok:
            n.notified_at = dt.datetime.now(tz=dt.timezone.utc)
        else:
            log.warning("E-Mail an %s fehlgeschlagen — bleibt in der Glocke", ziel)
        return {"kanal": "email", "ziel": ziel, "ok": ok}
    return {"kanal": "telegram", "ziel": n.chat_id or ""}


def _html(title: str, body: str) -> str:
    """Schlichtes HTML — der Text ist die Nachricht, nicht das Layout."""
    from html import escape
    zeilen = "<br>".join(escape(z) for z in (body or "").splitlines())
    return f"<p><b>{escape(title)}</b></p><p>{zeilen}</p>"


async def notify_issue(db: AsyncSession, issue: Issue, kind: str, title: str, body: str = "") -> None:
    owner_id = issue.assigned_by_user_id or issue.assignee_user_id or issue.reporter_id
    chat = None
    if owner_id:
        u = await db.get(User, owner_id)
        chat = (u.telegram_chat_id if u else None) or OWNER_CHAT or None
    else:
        chat = OWNER_CHAT or None
    db.add(Notification(user_id=owner_id, project_id=issue.project_id, issue_id=issue.id,
                        kind=kind, title=title, body=body[:4000], chat_id=chat))
