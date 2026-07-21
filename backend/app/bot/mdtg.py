"""Minimaler Markdown → Telegram-HTML-Konverter."""
from __future__ import annotations

import html
import re


def escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def md_to_html(text: str) -> str:
    out = escape(text or "")
    # Code-Spans zuerst (Inhalt nicht weiter formatieren)
    out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    # **bold** und __bold__
    out = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<b>{m.group(1)}</b>", out)
    out = re.sub(r"(?m)^#{1,6}\s*(.+)$", lambda m: f"<b>{m.group(1)}</b>", out)
    # *italic*
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: f"<i>{m.group(1)}</i>", out)
    return out


def clip(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def safe(text: str) -> str:
    return clip(md_to_html(text))
