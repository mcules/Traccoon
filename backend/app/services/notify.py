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

# Three ways out. The third is the open one: it calls a destination (base URL and login stand
# there), and what sits behind it — ntfy, Matrix, Gotify, a bot of one's own — is no longer
# Traccoon's business. A further messenger is thereby an entry under "destinations".
CHANNELS = ("telegram", "email", "ziel")


def _with_zone(ts: dt.datetime) -> dt.datetime:
    """Read a naive timestamp as UTC. SQLite hands them back without a zone."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=dt.timezone.utc)


def channel_address(user: User | None, channel: str) -> str:
    """The address this channel uses for this person, empty when there is none."""
    if user is None:
        return OWNER_CHAT if channel == "telegram" else ""
    if channel == "telegram":
        return user.telegram_chat_id or ""
    if channel == "email":
        return (user.notify_email or user.email or "").strip()
    if channel == "ziel":
        return str(user.notify_destination_id or "")
    return ""


def choose_channel(user: User | None, wanted: str = "") -> str:
    """Which channel is actually used.

    The sender's choice beats the person's default. If that channel has no address on
    file, the other one is used instead of dropping the message silently. A notification
    that reaches nobody is the worst outcome, worse than one on the second favourite
    channel.
    """
    order = [k for k in (wanted, (user.notify_default if user else ""), "telegram")
                   if k in CHANNELS]
    order += [k for k in CHANNELS if k not in order]
    for channel in order:
        if channel_address(user, channel):
            return channel
    return order[0]


# What a notification can hang off besides project and ticket. The bot decides by `kind`
# which buttons it attaches, and it finds the thing they act on over exactly these columns.
REFERENCES = ("issue_id", "assistant_task_id", "spam_verdict_id", "project_id")


async def deliver(db: AsyncSession, *, user: User | None, kind: str, title: str = "",
                    body: str = "", channel: str = "", project_id: int | None = None,
                    issue_id: int | None = None,
                    throttle_key: str = "", throttle_minutes: float = 0,
                    title_key: str = "", body_key: str = "",
                    values: dict[str, object] | None = None,
                    reference: dict[str, int] | None = None) -> dict:
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
    if throttle_key and throttle_minutes > 0:
        limit = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=throttle_minutes)
        last = (await db.execute(
            select(Notification.created_at)
            .where(Notification.throttle_key == throttle_key,
                   # Separated by recipient: two people using the same key must not
                   # mute each other.
                   Notification.user_id == (user.id if user else None),
                   Notification.created_at >= limit)
            .order_by(Notification.created_at.desc()).limit(1))).scalars().first()
        if last is not None:
            again = _with_zone(last) + dt.timedelta(minutes=throttle_minutes)
            log.info("throttled: %s (open again at %s)", throttle_key, again.isoformat())
            return {"kanal": "gedrosselt", "unterdrueckt": True, "drossel_key": throttle_key,
                    "wieder_ab": again.isoformat()}

    if title_key or body_key:
        from .i18n import tr
        language = getattr(user, "locale", None) or "de"
        if title_key:
            title = await tr(db, title_key, language, **(values or {}))
        if body_key:
            body = await tr(db, body_key, language, **(values or {}))

    chosen = choose_channel(user, channel)
    target = channel_address(user, chosen)
    n = Notification(user_id=(user.id if user else None), project_id=project_id,
                     issue_id=issue_id, kind=kind, title=title[:500], body=(body or "")[:4000],
                     throttle_key=(throttle_key or None),
                     chat_id=(target or OWNER_CHAT or None) if chosen == "telegram" else None)
    # A reference makes the message actionable: only with it does the bot know WHICH verdict
    # its "get it back" button undoes. Unknown keys are ignored instead of failing — the
    # sender is a flow, and a typo there must not tear down the notification.
    for field, value in (reference or {}).items():
        if field in REFERENCES and value is not None:
            setattr(n, field, int(value))
    db.add(n)

    if chosen == "ziel":
        # What goes out is the message itself as JSON. Whoever needs a different format hangs
        # it on the destination (path, headers, login) — that is exactly what destinations are for.
        from ..models.destination import Destination
        from . import destinations
        dest = await db.get(Destination, int(target)) if target else None
        if dest is None or not dest.enabled:
            log.warning("no (active) destination for user %s, bell only",
                        user.id if user else None)
            return {"kanal": "bell", "grund": "kein Ziel"}
        try:
            answer = await destinations.call(
                db, dest, method="POST",
                body={"art": kind, "titel": title, "text": body or title})
            n.notified_at = dt.datetime.now(tz=dt.timezone.utc)
            return {"kanal": "ziel", "ziel": dest.name, "ok": True,
                    "status": answer.get("status_code")}
        except Exception as e:  # noqa: BLE001 — the bell carries the message anyway
            log.warning("destination %s failed (%s), stays in the bell", dest.name, e)
            return {"kanal": "ziel", "ziel": dest.name, "ok": False}

    if chosen == "email":
        if not target:
            log.warning("no email address for user %s, bell only",
                        user.id if user else None)
            return {"kanal": "bell", "grund": "keine Adresse"}
        from . import mail
        ok = await mail.send_mail(db, target, title[:200] or "Traccoon",
                                  html_body=_html(title, body), text_body=body or title)
        if ok:
            n.notified_at = dt.datetime.now(tz=dt.timezone.utc)
        else:
            log.warning("email to %s failed, stays in the bell", target)
        return {"kanal": "email", "ziel": target, "ok": ok}
    return {"kanal": "telegram", "ziel": n.chat_id or ""}


def _html(title: str, body: str) -> str:
    """Plain HTML. The text is the message, not the layout."""
    from html import escape
    lines = "<br>".join(escape(z) for z in (body or "").splitlines())
    return f"<p><b>{escape(title)}</b></p><p>{lines}</p>"


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
