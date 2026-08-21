"""Traccoon as an MCP server for its own mailboxes.

Until now Traccoon only used foreign MCP servers. Here it offers some itself for the first
time — exactly what the old `imap-mcp` could do, but with the accounts a person maintains in
Traccoon instead of the ones from the `.env` of a stack.

Three rules that make the difference to a mailbox login:

* **Nothing is open until somebody opens it.** Which tools exist is released per account. An
  account without a release does not exist for agents.
* **Folders can disappear.** What stands in the ignore list turns up neither in the folder
  list nor in a search — the private mailbox in the same account is nobody's business, not
  even one's own agent's.
* **Writing is a step of its own.** Reading, refiling and sending are three
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

# The catalog. `kind` is the permission behind it — it stands here so the UI can show it
# without knowing it a second time.
TOOLS: list[dict] = [
    {"name": "mail_accounts", "art": "lesen", "immer": True,
     "description": "List the released mailboxes.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "mail_folders", "art": "lesen",
     "description": "The folders of a mailbox (without the ignored ones).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string", "description": "Name des Postfachs"}},
         "required": ["account"]}},
    {"name": "mail_search", "art": "lesen",
     "description": "Nachrichten eines Ordners auflisten oder durchsuchen (Volltext).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "query": {"type": "string", "description": "Volltext, leer = alle"},
         "limit": {"type": "integer", "default": 25},
         "offset": {"type": "integer", "default": 0}},
         "required": ["account"]}},
    {"name": "mail_get", "art": "lesen",
     "description": "Fetch a message with its text and attachment list.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}},
         "required": ["account", "uid"]}},
    {"name": "mail_attachment", "art": "lesen",
     "description": "Einen Anhang als Base64 holen.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}, "index": {"type": "integer"}},
         "required": ["account", "uid", "index"]}},
    {"name": "mail_flag", "art": "aendern",
     "description": "Mark a message as read, unread or flagged.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"},
         "flag": {"type": "string", "enum": ["seen", "flagged"], "default": "seen"},
         "on": {"type": "boolean", "default": True}},
         "required": ["account", "uid"]}},
    {"name": "mail_move", "art": "aendern",
     "description": "Eine Nachricht in einen anderen Ordner verschieben.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}, "target": {"type": "string"}},
         "required": ["account", "uid", "target"]}},
    {"name": "mail_archive", "art": "aendern",
     "description": "Eine Nachricht archivieren (Ordner bzw. Muster des Kontos).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}},
         "required": ["account", "uid"]}},
    {"name": "mail_spam", "art": "aendern",
     "description": "Move a message into the spam folder of the account.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "folder": {"type": "string", "default": "INBOX"},
         "uid": {"type": "integer"}},
         "required": ["account", "uid"]}},
    {"name": "mail_draft", "art": "senden",
     "description": "Einen Entwurf im Postfach ablegen (verschickt nichts).",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "identity": {"type": "string"},
         "to": {"type": "array", "items": {"type": "string"}},
         "cc": {"type": "array", "items": {"type": "string"}},
         "subject": {"type": "string"}, "text": {"type": "string"}},
         "required": ["account", "to"]}},
    {"name": "mail_send", "art": "senden",
     "description": "Eine Nachricht wirklich verschicken.",
     "schema": {"type": "object", "properties": {
         "account": {"type": "string"}, "identity": {"type": "string"},
         "to": {"type": "array", "items": {"type": "string"}},
         "cc": {"type": "array", "items": {"type": "string"}},
         "subject": {"type": "string"}, "text": {"type": "string"},
         "in_reply_to": {"type": "string"}},
         "required": ["account", "to"]}},
]
BY_NAME = {w["name"]: w for w in TOOLS}


def ignores(name: str, pattern: list) -> bool:
    """Is this folder invisible to tools? Patterns like `Privat*` or `INBOX.Familie`."""
    for m in pattern or []:
        m = str(m).strip()
        if m and (fnmatch.fnmatch(name, m) or fnmatch.fnmatch(name.lower(), m.lower())):
            return True
    return False


async def accounts(db: AsyncSession, user: User) -> list[MailAccount]:
    rows = (await db.execute(select(MailAccount).where(
        MailAccount.owner_user_id == user.id,
        MailAccount.enabled.is_(True),
        MailAccount.mcp_enabled.is_(True)))).scalars().all()
    return list(rows)


async def toollist(db: AsyncSession, user: User) -> list[dict]:
    """What this access may offer: the union of the releases of all accounts.

    Which account allows which tool is decided at call time — here stands only what exists at
    all. Otherwise an agent would have to guess why a tool is missing.
    """
    free = {"mail_accounts"}
    for account in await accounts(db, user):
        free.update(account.mcp_tools or [])
    return [{"name": w["name"], "description": w["description"], "inputSchema": w["schema"]}
            for w in TOOLS if w["name"] in free]


async def instructions(db: AsyncSession, user: User) -> str:
    """The house rules of all released mailboxes, for the `instructions` field of the
    protocol: it is read on connecting, so before the first tool runs."""
    parts = []
    for k in await accounts(db, user):
        if k.mcp_instructions:
            parts.append(f"Postfach „{k.name}\": {k.mcp_instructions.strip()}")
    if not parts:
        return ""
    return ("House rules of the released mailboxes — they apply before anything else:\n\n"
            + "\n\n".join(parts))


async def _account(db: AsyncSession, user: User, name: str, tool: str) -> MailAccount:
    for account in await accounts(db, user):
        if account.name == name:
            if tool != "mail_accounts" and tool not in (account.mcp_tools or []):
                raise PermissionError(
                    f"The mailbox \"{name}\" does not release \"{tool}\"")
            return account
    raise LookupError(f"Kein freigegebenes Postfach „{name}\"")


async def execute(db: AsyncSession, user: User, name: str, args: dict) -> Any:
    """Run a tool. Everything that arrives here has already passed the releases."""
    if name not in BY_NAME:
        raise LookupError(f"Unbekanntes Werkzeug „{name}\"")

    if name == "mail_accounts":
        out = []
        for k in await accounts(db, user):
            entry = {"name": k.name, "tools": sorted(k.mcp_tools or []),
                       "ignored_folders": list(k.mcp_ignore_folders or [])}
            # The house rules of the mailbox belong at the place where an agent gets to know
            # it — not into a file it might read.
            if k.mcp_instructions:
                entry["instructions"] = k.mcp_instructions
            out.append(entry)
        return out

    account = await _account(db, user, str(args.get("account") or ""), name)
    folder = str(args.get("folder") or "INBOX")
    if name != "mail_folders" and ignores(folder, account.mcp_ignore_folders):
        raise PermissionError(f"The folder \"{folder}\" is blocked for tools")

    if name == "mail_folders":
        all_rows = await mailbox.folder(account, count=True)
        return [o for o in all_rows if not ignores(o["name"], account.mcp_ignore_folders)]

    if name == "mail_search":
        return await mailbox.listing(account, folder, str(args.get("query") or ""),
                                   int(args.get("offset") or 0),
                                   max(1, min(int(args.get("limit") or 25), 100)))

    if name == "mail_get":
        return await mailbox.message(account, folder, int(args["uid"]))

    if name == "mail_attachment":
        import base64
        file, kind, data = await mailbox.attachment(account, folder, int(args["uid"]),
                                                 int(args["index"]))
        return {"filename": file, "content_type": kind, "size": len(data),
                "base64": base64.b64encode(data).decode()}

    if name == "mail_flag":
        flag = "\\\\Seen" if str(args.get("flag") or "seen") == "seen" else "\\\\Flagged"
        await mailbox.flag(account, folder, int(args["uid"]), flag,
                           bool(args.get("on", True)))
        return {"ok": True}

    if name == "mail_move":
        target = str(args["target"])
        # A blocked folder is no target either — otherwise the ignore list would be a screen
        # one can push mail through.
        if ignores(target, account.mcp_ignore_folders):
            raise PermissionError(f"The folder \"{target}\" is blocked for tools")
        await mailbox.move(account, folder, int(args["uid"]), target)
        return {"ok": True, "folder": target}

    if name == "mail_archive":
        return {"ok": True, "folder": await mailbox.archive(account, folder,
                                                                int(args["uid"]))}

    if name == "mail_spam":
        if not account.folder_junk:
            raise ValueError("Dieses Postfach hat keinen Spam-Ordner")
        await mailbox.move(account, folder, int(args["uid"]), account.folder_junk)
        return {"ok": True, "folder": account.folder_junk}

    if name in ("mail_send", "mail_draft"):
        ident = await _identity(db, account, str(args.get("identity") or ""))
        fields = {"to": list(args.get("to") or []), "cc": list(args.get("cc") or []),
                  "subject": str(args.get("subject") or ""),
                  "text": str(args.get("text") or ""),
                  "in_reply_to": str(args.get("in_reply_to") or ""), "attachments": []}
        if not fields["to"]:
            raise ValueError("Nothing goes out without a recipient")
        if name == "mail_draft":
            await mailbox.draft_save(account, ident, fields)
            return {"ok": True, "draft": True}
        await mailbox.send(account, ident, fields)
        log.info("MCP: mail from %s through account %s to %s", user.id, account.name, fields["to"])
        return {"ok": True, "sent": True}

    raise LookupError(f"Unbekanntes Werkzeug „{name}\"")


async def _identity(db: AsyncSession, account: MailAccount, wish: str) -> MailIdentity:
    rows = (await db.execute(select(MailIdentity).where(
        MailIdentity.account_id == account.id))).scalars().all()
    if not rows:
        raise ValueError(f"The mailbox \"{account.name}\" has no identity")
    if wish:
        for i in rows:
            if i.email.lower() == wish.lower():
                return i
        raise ValueError(f"No identity \"{wish}\" on this mailbox")
    return next((i for i in rows if i.is_default), rows[0])
