"""Traccoon als MCP-Server für die eigenen Postfächer.

Bisher hat Traccoon nur fremde MCP-Server benutzt. Hier bietet es zum ersten Mal selbst
welche an — und zwar genau das, was der alte `imap-mcp` konnte, aber mit den Konten, die
eine Person in Traccoon pflegt statt mit denen aus der `.env` eines Stacks.

Drei Regeln, die den Unterschied zu einem Postfach-Zugang ausmachen:

* **Nichts ist offen, bis es jemand öffnet.** Je Konto wird einzeln freigegeben, welche
  Werkzeuge es gibt. Ein Konto ohne Freigabe existiert für Agenten nicht.
* **Ordner können verschwinden.** Was in der Ignorierliste steht, taucht weder in der
  Ordnerliste noch in einer Suche auf — das private Postfach im selben Konto geht niemanden
  etwas an, auch keinen eigenen Agenten.
* **Schreiben ist ein eigener Schritt.** Lesen, Umsortieren und Senden sind drei
  verschiedene Freigaben, keine Stufen einer Leiter.
"""
from __future__ import annotations

import fnmatch
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.mail import MailAccount, MailIdentity
from ..models.user import User
from . import mailbox

log = logging.getLogger("mail_mcp")

# Der Katalog. `art` ist die Berechtigung, die dahinter steckt — sie steht hier, damit die
# Oberfläche sie anzeigen kann, ohne sie ein zweites Mal zu wissen.
TOOLS: list[dict] = [
    {"name": "mail_accounts", "art": "lesen", "immer": True,
     "beschreibung": "Freigegebene Postfächer auflisten.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "mail_folders", "art": "lesen",
     "beschreibung": "Ordner eines Postfachs (ohne die ignorierten).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string", "description": "Name des Postfachs"}},
         "required": ["account"]}},
    {"name": "mail_search", "art": "lesen",
     "beschreibung": "Nachrichten eines Ordners auflisten oder durchsuchen (Volltext).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "query": {"type": "string", "description": "Volltext, leer = alle"},
         "limit": {"type": "integer", "default": 25},
         "offset": {"type": "integer", "default": 0}},
         "required": ["account"]}},
    {"name": "mail_get", "art": "lesen",
     "beschreibung": "Eine Nachricht mit Text und Anhangliste holen.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}},
         "required": ["account", "uid"]}},
    {"name": "mail_attachment", "art": "lesen",
     "beschreibung": "Einen Anhang als Base64 holen.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}, "index": {"type": "integer"}},
         "required": ["account", "uid", "index"]}},
    {"name": "mail_flag", "art": "aendern",
     "beschreibung": "Eine Nachricht als gelesen/ungelesen oder markiert kennzeichnen.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"},
         "flag": {"type": "string", "enum": ["seen", "flagged"], "default": "seen"},
         "on": {"type": "boolean", "default": True}},
         "required": ["account", "uid"]}},
    {"name": "mail_move", "art": "aendern",
     "beschreibung": "Eine Nachricht in einen anderen Ordner verschieben.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}, "target": {"type": "string"}},
         "required": ["account", "uid", "target"]}},
    {"name": "mail_archive", "art": "aendern",
     "beschreibung": "Eine Nachricht archivieren (Ordner bzw. Muster des Kontos).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}},
         "required": ["account", "uid"]}},
    {"name": "mail_spam", "art": "aendern",
     "beschreibung": "Eine Nachricht in den Spam-Ordner des Kontos verschieben.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}},
         "required": ["account", "uid"]}},
    {"name": "mail_draft", "art": "senden",
     "beschreibung": "Einen Entwurf im Postfach ablegen (verschickt nichts).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "identity": {"type": "string"},
         "to": {"type": "array", "items": {"type": "string"}},
         "cc": {"type": "array", "items": {"type": "string"}},
         "subject": {"type": "string"}, "text": {"type": "string"}},
         "required": ["account", "to"]}},
    {"name": "mail_send", "art": "senden",
     "beschreibung": "Eine Nachricht wirklich verschicken.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "identity": {"type": "string"},
         "to": {"type": "array", "items": {"type": "string"}},
         "cc": {"type": "array", "items": {"type": "string"}},
         "subject": {"type": "string"}, "text": {"type": "string"},
         "in_reply_to": {"type": "string"}},
         "required": ["account", "to"]}},
]
NACH_NAME = {w["name"]: w for w in TOOLS}


def ignoriert(name: str, pattern: list) -> bool:
    """Ist dieser Ordner für Werkzeuge unsichtbar? Muster wie `Privat*` oder `INBOX.Familie`."""
    for m in pattern or []:
        m = str(m).strip()
        if m and (fnmatch.fnmatch(name, m) or fnmatch.fnmatch(name.lower(), m.lower())):
            return True
    return False


async def konten(db: AsyncSession, user: User) -> list[MailAccount]:
    rows = (await db.execute(select(MailAccount).where(
        MailAccount.owner_user_id == user.id,
        MailAccount.enabled.is_(True),
        MailAccount.mcp_enabled.is_(True)))).scalars().all()
    return list(rows)


async def werkzeugliste(db: AsyncSession, user: User) -> list[dict]:
    """Was dieser Zugang anbieten darf: die Vereinigung der Freigaben aller Konten.

    Welches Konto welches Werkzeug erlaubt, entscheidet sich beim Aufruf — hier steht nur,
    was überhaupt existiert. Sonst müsste ein Agent raten, warum ein Werkzeug fehlt.
    """
    frei = {"mail_accounts"}
    for konto in await konten(db, user):
        frei.update(konto.mcp_tools or [])
    return [{"name": w["name"], "description": w["beschreibung"], "inputSchema": w["schema"]}
            for w in TOOLS if w["name"] in frei]


async def anweisungen(db: AsyncSession, user: User) -> str:
    """Die Hausregeln aller freigegebenen Postfächer, für das `instructions`-Feld des
    Protokolls: Es wird beim Verbinden gelesen, also noch bevor das erste Werkzeug läuft."""
    parts = []
    for k in await konten(db, user):
        if k.mcp_instructions:
            parts.append(f"Postfach „{k.name}\": {k.mcp_instructions.strip()}")
    if not parts:
        return ""
    return ("Hausregeln der freigegebenen Postfächer — sie gelten vor allem anderen:\n\n"
            + "\n\n".join(parts))


async def _konto(db: AsyncSession, user: User, name: str, tool: str) -> MailAccount:
    for konto in await konten(db, user):
        if konto.name == name:
            if tool != "mail_accounts" and tool not in (konto.mcp_tools or []):
                raise PermissionError(
                    f"Das Postfach „{name}\" gibt „{tool}\" nicht frei")
            return konto
    raise LookupError(f"Kein freigegebenes Postfach „{name}\"")


async def ausfuehren(db: AsyncSession, user: User, name: str, args: dict) -> Any:
    """Ein Werkzeug ausführen. Alles, was hier ankommt, ist bereits durch die Freigaben."""
    if name not in NACH_NAME:
        raise LookupError(f"Unbekanntes Werkzeug „{name}\"")

    if name == "mail_accounts":
        out = []
        for k in await konten(db, user):
            entry = {"name": k.name, "tools": sorted(k.mcp_tools or []),
                       "ignored_folders": list(k.mcp_ignore_folders or [])}
            # Die Hausregeln des Postfachs gehören an die Stelle, an der ein Agent es
            # kennenlernt — nicht in eine Datei, die er vielleicht liest.
            if k.mcp_instructions:
                entry["instructions"] = k.mcp_instructions
            out.append(entry)
        return out

    konto = await _konto(db, user, str(args.get("account") or ""), name)
    folder = str(args.get("folder") or "INBOX")
    if name != "mail_folders" and ignoriert(folder, konto.mcp_ignore_folders):
        raise PermissionError(f"Der Ordner „{folder}\" ist für Werkzeuge gesperrt")

    if name == "mail_folders":
        alle = await mailbox.folder(konto, count=True)
        return [o for o in alle if not ignoriert(o["name"], konto.mcp_ignore_folders)]

    if name == "mail_search":
        return await mailbox.listing(konto, folder, str(args.get("query") or ""),
                                   int(args.get("offset") or 0),
                                   max(1, min(int(args.get("limit") or 25), 100)))

    if name == "mail_get":
        return await mailbox.message(konto, folder, int(args["uid"]))

    if name == "mail_attachment":
        import base64
        file, kind, daten = await mailbox.attachment(konto, folder, int(args["uid"]),
                                                 int(args["index"]))
        return {"filename": file, "content_type": kind, "size": len(daten),
                "base64": base64.b64encode(daten).decode()}

    if name == "mail_flag":
        flagge = "\\\\Seen" if str(args.get("flag") or "seen") == "seen" else "\\\\Flagged"
        await mailbox.flag(konto, folder, int(args["uid"]), flagge,
                           bool(args.get("on", True)))
        return {"ok": True}

    if name == "mail_move":
        target = str(args["target"])
        # Ein gesperrter Ordner ist auch kein Ziel — sonst wäre die Ignorierliste ein
        # Sichtschutz, durch den man Post schieben kann.
        if ignoriert(target, konto.mcp_ignore_folders):
            raise PermissionError(f"Der Ordner „{target}\" ist für Werkzeuge gesperrt")
        await mailbox.move(konto, folder, int(args["uid"]), target)
        return {"ok": True, "folder": target}

    if name == "mail_archive":
        return {"ok": True, "folder": await mailbox.archivieren(konto, folder,
                                                                int(args["uid"]))}

    if name == "mail_spam":
        if not konto.folder_junk:
            raise ValueError("Dieses Postfach hat keinen Spam-Ordner")
        await mailbox.move(konto, folder, int(args["uid"]), konto.folder_junk)
        return {"ok": True, "folder": konto.folder_junk}

    if name in ("mail_send", "mail_draft"):
        ident = await _identity(db, konto, str(args.get("identity") or ""))
        fields = {"to": list(args.get("to") or []), "cc": list(args.get("cc") or []),
                  "subject": str(args.get("subject") or ""),
                  "text": str(args.get("text") or ""),
                  "in_reply_to": str(args.get("in_reply_to") or ""), "attachments": []}
        if not fields["to"]:
            raise ValueError("Ohne Empfänger geht nichts raus")
        if name == "mail_draft":
            await mailbox.entwurf_speichern(konto, ident, fields)
            return {"ok": True, "draft": True}
        await mailbox.senden(konto, ident, fields)
        log.info("MCP: Mail von %s über Konto %s an %s", user.id, konto.name, fields["to"])
        return {"ok": True, "sent": True}

    raise LookupError(f"Unbekanntes Werkzeug „{name}\"")


async def _identity(db: AsyncSession, konto: MailAccount, wunsch: str) -> MailIdentity:
    rows = (await db.execute(select(MailIdentity).where(
        MailIdentity.account_id == konto.id))).scalars().all()
    if not rows:
        raise ValueError(f"Das Postfach „{konto.name}\" hat keine Identität")
    if wunsch:
        for i in rows:
            if i.email.lower() == wunsch.lower():
                return i
        raise ValueError(f"Keine Identität „{wunsch}\" an diesem Postfach")
    return next((i for i in rows if i.is_default), rows[0])
