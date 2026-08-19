"""Notifications: the bell always, the way out chosen by the person.

There used to be exactly one way out, the messenger, provided a chat id was on file. But
whoever triggers a notification rarely knows whether the recipient uses it at all, and
inside a flow the recipient is often only known at runtime. So the person decides how they
are reached (`users.notify_default`), and the sender may name a channel but does not have
to.

The bell is independent of that: every notification is also a row in the UI. The channel
only decides what goes out on top of it.
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
    """Read a naive timestamp as UTC. SQLite hands them back without a zone."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=dt.timezone.utc)


def kanal_adresse(user: User | None, kanal: str) -> str:
    """The address this channel uses for this person, empty when there is none."""
    if user is None:
        return OWNER_CHAT if kanal == "telegram" else ""
    if kanal == "telegram":
        return user.telegram_chat_id or ""
    if kanal == "email":
        return (user.notify_email or user.email or "").strip()
    return ""


def waehle_kanal(user: User | None, gewuenscht: str = "") -> str:
    """Which channel is actually used.

    The sender's choice beats the person's default. If that channel has no address on
    file, the other one is used instead of dropping the message silently. A notification
    that reaches nobody is the worst outcome, worse than one on the second favourite
    channel.
    """
    reihenfolge = [k for k in (gewuenscht, (user.notify_default if user else ""), "telegram")
                   if k in KANAELE]
    reihenfolge += [k for k in KANAELE if k not in reihenfolge]
    for kanal in reihenfolge:
        if kanal_adresse(user, kanal):
            return kanal
    return reihenfolge[0]


# What a notification can hang off besides project and ticket. The bot decides by `kind`
# which buttons it attaches, and it finds the thing they act on over exactly these columns.
BEZUEGE = ("issue_id", "assistant_task_id", "spam_verdict_id", "project_id")


async def zustellen(db: AsyncSession, *, user: User | None, kind: str, title: str = "",
                    body: str = "", kanal: str = "", project_id: int | None = None,
                    issue_id: int | None = None,
                    drossel_key: str = "", drossel_minuten: float = 0,
                    title_key: str = "", body_key: str = "",
                    werte: dict[str, object] | None = None,
                    bezug: dict[str, int] | None = None) -> dict:
    """Create a notification and send it out on the fitting channel.

    The messenger is still handled by the bot process, the only one holding the bot token,
    so all that happens here is setting the chat id. Email goes out right away: no second
    process is needed, and `notified_at` tells the bell that nothing is pending outside.

    `title_key` and `body_key` name a text in the server catalog; it is rendered in the
    language of the recipient. Whoever passes `title` directly gets that text as it stands,
    which is what a flow needs: its notification is written by a person and belongs to nobody
    else's language.

    With `drossel_key` and `drossel_minuten` the same message is suppressed inside the
    window, completely, including the bell. Putting it there only would push the noise one
    floor down: 120 identical rows make a list with an unread counter as useless as 120
    messages. It stays traceable anyway, because the step in the flow records that it was
    throttled.
    """
    if drossel_key and drossel_minuten > 0:
        grenze = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=drossel_minuten)
        letzte = (await db.execute(
            select(Notification.created_at)
            .where(Notification.drossel_key == drossel_key,
                   # Separated by recipient: two people using the same key must not
                   # mute each other.
                   Notification.user_id == (user.id if user else None),
                   Notification.created_at >= grenze)
            .order_by(Notification.created_at.desc()).limit(1))).scalars().first()
        if letzte is not None:
            wieder = _mit_zone(letzte) + dt.timedelta(minutes=drossel_minuten)
            log.info("throttled: %s (open again at %s)", drossel_key, wieder.isoformat())
            return {"kanal": "gedrosselt", "unterdrueckt": True, "drossel_key": drossel_key,
                    "wieder_ab": wieder.isoformat()}

    if title_key or body_key:
        from .i18n import tr
        sprache = getattr(user, "locale", None) or "de"
        if title_key:
            title = await tr(db, title_key, sprache, **(werte or {}))
        if body_key:
            body = await tr(db, body_key, sprache, **(werte or {}))

    gewaehlt = waehle_kanal(user, kanal)
    ziel = kanal_adresse(user, gewaehlt)
    n = Notification(user_id=(user.id if user else None), project_id=project_id,
                     issue_id=issue_id, kind=kind, title=title[:500], body=(body or "")[:4000],
                     drossel_key=(drossel_key or None),
                     chat_id=(ziel or OWNER_CHAT or None) if gewaehlt == "telegram" else None)
    # A reference makes the message actionable: only with it does the bot know WHICH verdict
    # its "get it back" button undoes. Unknown keys are ignored instead of failing — the
    # sender is a flow, and a typo there must not tear down the notification.
    for feld, wert in (bezug or {}).items():
        if feld in BEZUEGE and wert is not None:
            setattr(n, feld, int(wert))
    db.add(n)

    if gewaehlt == "email":
        if not ziel:
            log.warning("no email address for user %s, bell only",
                        user.id if user else None)
            return {"kanal": "bell", "grund": "keine Adresse"}
        from . import mail
        ok = await mail.send_mail(db, ziel, title[:200] or "Traccoon",
                                  html_body=_html(title, body), text_body=body or title)
        if ok:
            n.notified_at = dt.datetime.now(tz=dt.timezone.utc)
        else:
            log.warning("email to %s failed, stays in the bell", ziel)
        return {"kanal": "email", "ziel": ziel, "ok": ok}
    return {"kanal": "telegram", "ziel": n.chat_id or ""}


def _html(title: str, body: str) -> str:
    """Plain HTML. The text is the message, not the layout."""
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
