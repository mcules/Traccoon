"""Simple SMTP mail sending (for project invitations for instance).

The configuration comes primarily from AppSettings (database, changeable over the admin UI),
with a fallback to the environment (config.py). It runs synchronously in a thread (no
aiosmtplib dependency needed; smtplib from the standard library is enough for occasional sending)."""
from __future__ import annotations

import asyncio
import email.message
import logging
import smtplib

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.security import decrypt_secret, encrypt_secret
from .appsettings import get_setting, set_setting

log = logging.getLogger("traccoon.mail")

_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_use_tls")


async def get_mail_config(db: AsyncSession) -> dict:
    """SMTP configuration: database values (AppSettings) override the environment defaults."""
    cfg = {
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_user": settings.smtp_user,
        "smtp_password": settings.smtp_password,
        "smtp_from": settings.smtp_from or settings.smtp_user,
        "smtp_use_tls": settings.smtp_use_tls,
    }
    for key in _KEYS:
        val = await get_setting(db, key, "")
        if val != "":
            if key == "smtp_port":
                cfg[key] = int(val) if val.isdigit() else cfg[key]
            elif key == "smtp_use_tls":
                cfg[key] = val.lower() in ("1", "true", "yes")
            elif key == "smtp_password":
                cfg[key] = decrypt_secret(val)
            else:
                cfg[key] = val
    if not cfg["smtp_from"]:
        cfg["smtp_from"] = cfg["smtp_user"]
    return cfg


async def set_mail_config(db: AsyncSession, data: dict) -> None:
    for key in _KEYS:
        if key in data and data[key] is not None:
            val = str(data[key])
            if key == "smtp_password" and val:
                val = encrypt_secret(val)
            await set_setting(db, key, val)


def _send_sync(cfg: dict, to_addr: str, subject: str, html_body: str, text_body: str) -> None:
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_from"] or "traccoon@localhost"
    msg["To"] = to_addr
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=15) as s:
        if cfg["smtp_use_tls"]:
            s.starttls()
        if cfg["smtp_user"]:
            s.login(cfg["smtp_user"], cfg["smtp_password"])
        s.send_message(msg)


async def send_mail(db: AsyncSession, to_addr: str, subject: str, html_body: str, text_body: str) -> bool:
    """Sends a mail. Without a configured SMTP host it is only logged (a dev fallback)."""
    cfg = await get_mail_config(db)
    if not cfg["smtp_host"]:
        log.warning("SMTP not configured, mail to %s is NOT sent (only logged): %s",
                    to_addr, subject)
        return False
    try:
        await asyncio.to_thread(_send_sync, cfg, to_addr, subject, html_body, text_body)
        return True
    except Exception:
        log.exception("Sending mail to %s failed", to_addr)
        return False
