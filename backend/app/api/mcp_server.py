"""The MCP access to one's own mailboxes.

Traccoon has only used foreign MCP servers so far; this is the first one it offers itself. The
protocol is deliberately served by hand instead of with a library: three methods are needed
(`initialize`, `tools/list`, `tools/call`), and pulling a dependency with a server model of its
own into an existing FastAPI for that would be more scaffolding than benefit.

Logging in happens with a token of the person (`Authorization: Bearer …`). It is no login:
whoever has it may do exactly what this person has released on their mailboxes — not
mehr, und nichts anderes in Traccoon.
"""
import hashlib
import hmac
import logging
import secrets

from fastapi import APIRouter, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decrypt_secret, encrypt_secret
from ..db import get_session
from ..models.user import User
from ..services import mail_mcp

from fastapi import Depends

from .deps import get_current_user

log = logging.getLogger("mcp_server")
router = APIRouter(tags=["mcp-server"])

LOG = "2024-11-05"


async def _person(db: AsyncSession, header: str | None) -> User:
    """The person behind the token. Compared in constant time, not with `==`."""
    raw = (header or "").removeprefix("Bearer ").strip()
    if not raw:
        raise PermissionError("kein Token")
    # The tokens are stored encrypted; there are few of them (one per person), so walking
    # through them is enough. A hash index would be faster and still only ballast here.
    rows = (await db.execute(select(User).where(User.mail_mcp_token_enc != ""))).scalars().all()
    for u in rows:
        if hmac.compare_digest(decrypt_secret(u.mail_mcp_token_enc), raw):
            return u
    raise PermissionError("unbekanntes Token")


def _answer(id_, result=None, error=None) -> dict:
    if error is not None:
        return {"jsonrpc": "2.0", "id": id_, "error": error}
    return {"jsonrpc": "2.0", "id": id_, "result": result}


@router.post("/mcp/mail")
async def mcp_mail(request: Request, authorization: str | None = Header(default=None),
                   db: AsyncSession = Depends(get_session)):
    """One call of the MCP protocol (JSON-RPC 2.0 over HTTP)."""
    try:
        message = await request.json()
    except Exception:  # noqa: BLE001
        return _answer(None, error={"code": -32700, "message": "Kein gültiges JSON"})

    methode = str(message.get("method") or "")
    id_ = message.get("id")
    params = message.get("params") or {}

    # Notifications (without an id) are acknowledged, not answered.
    if methode.startswith("notifications/"):
        return {}

    try:
        user = await _person(db, authorization)
    except PermissionError as exc:
        return _answer(id_, error={"code": -32001, "message": str(exc)})

    if methode == "initialize":
        result = {
            "protocolVersion": LOG,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "traccoon-mail", "version": "1"},
        }
        # `instructions` belongs to the protocol and is read on connecting — so before the
        # first tool runs. That is exactly where house rules belong.
        hinweise = await mail_mcp.instructions(db, user)
        if hinweise:
            result["instructions"] = hinweise
        return _answer(id_, result)

    if methode == "tools/list":
        return _answer(id_, {"tools": await mail_mcp.toollist(db, user)})

    if methode == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            result = await mail_mcp.execute(db, user, name, args)
        except PermissionError as exc:
            # A block is no crash: the agent should be able to read why it does not work,
            # statt es als Serverfehler zu behandeln und wieder zu versuchen.
            return _answer(id_, {"content": [{"type": "text", "text": f"Nicht erlaubt: {exc}"}],
                                  "isError": True})
        except (LookupError, ValueError) as exc:
            return _answer(id_, {"content": [{"type": "text", "text": str(exc)}],
                                  "isError": True})
        except Exception as exc:  # noqa: BLE001
            log.exception("MCP-Werkzeug %s gescheitert", name)
            return _answer(id_, {"content": [{"type": "text", "text": f"Fehler: {exc}"}],
                                  "isError": True})
        import json
        return _answer(id_, {"content": [{"type": "text",
                                           "text": json.dumps(result, ensure_ascii=False,
                                                              default=str)}]})

    return _answer(id_, error={"code": -32601, "message": f"Unbekannte Methode {methode}"})


# ── Manage the token (from the UI) ──────────────────────────────────────────

@router.post("/mailbox/mcp-token")
async def token_renew(user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """Creates a new token and shows it ONCE. An old one becomes invalid with it."""
    raw = "trmcp_" + secrets.token_urlsafe(32)
    user.mail_mcp_token_enc = encrypt_secret(raw)
    await db.commit()
    return {"token": raw}


@router.get("/mailbox/mcp-status")
async def token_state(user: User = Depends(get_current_user)):
    """Only whether one exists — the token itself never comes out again."""
    return {"token_set": bool(user.mail_mcp_token_enc),
            "fingerprint": (hashlib.sha256(user.mail_mcp_token_enc.encode()).hexdigest()[:8]
                            if user.mail_mcp_token_enc else "")}


@router.delete("/mailbox/mcp-token", status_code=204)
async def token_delete(user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    user.mail_mcp_token_enc = ""
    await db.commit()
