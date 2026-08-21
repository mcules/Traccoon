"""Der MCP-Zugang zu den eigenen Postfächern.

Traccoon hat bisher nur fremde MCP-Server benutzt; das hier ist der erste, den es selbst
anbietet. Das Protokoll ist bewusst von Hand bedient statt mit einer Bibliothek: gebraucht
werden drei Methoden (`initialize`, `tools/list`, `tools/call`), und dafür eine Abhängigkeit
mit eigenem Server-Modell in ein bestehendes FastAPI zu ziehen, wäre mehr Aufbau als Nutzen.

Angemeldet wird mit einem Token der Person (`Authorization: Bearer …`). Es ist kein Login:
Wer es hat, darf genau das, was diese Person an ihren Postfächern freigegeben hat — nicht
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

PROTOKOLL = "2024-11-05"


async def _person(db: AsyncSession, header: str | None) -> User:
    """Die Person hinter dem Token. Verglichen wird in konstanter Zeit, nicht mit `==`."""
    roh = (header or "").removeprefix("Bearer ").strip()
    if not roh:
        raise PermissionError("kein Token")
    # Die Tokens liegen verschlüsselt; es sind wenige (eines je Person), also reicht das
    # Durchgehen. Ein Hash-Index wäre schneller und hier trotzdem nur Ballast.
    rows = (await db.execute(select(User).where(User.mail_mcp_token_enc != ""))).scalars().all()
    for u in rows:
        if hmac.compare_digest(decrypt_secret(u.mail_mcp_token_enc), roh):
            return u
    raise PermissionError("unbekanntes Token")


def _answer(id_, result=None, error=None) -> dict:
    if error is not None:
        return {"jsonrpc": "2.0", "id": id_, "error": error}
    return {"jsonrpc": "2.0", "id": id_, "result": result}


@router.post("/mcp/mail")
async def mcp_mail(request: Request, authorization: str | None = Header(default=None),
                   db: AsyncSession = Depends(get_session)):
    """Ein Aufruf des MCP-Protokolls (JSON-RPC 2.0 über HTTP)."""
    try:
        message = await request.json()
    except Exception:  # noqa: BLE001
        return _answer(None, error={"code": -32700, "message": "Kein gültiges JSON"})

    methode = str(message.get("method") or "")
    id_ = message.get("id")
    params = message.get("params") or {}

    # Benachrichtigungen (ohne id) werden quittiert, nicht beantwortet.
    if methode.startswith("notifications/"):
        return {}

    try:
        user = await _person(db, authorization)
    except PermissionError as exc:
        return _answer(id_, error={"code": -32001, "message": str(exc)})

    if methode == "initialize":
        result = {
            "protocolVersion": PROTOKOLL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "traccoon-mail", "version": "1"},
        }
        # `instructions` gehört zum Protokoll und wird beim Verbinden gelesen — also noch
        # bevor das erste Werkzeug läuft. Genau dort gehören Hausregeln hin.
        hinweise = await mail_mcp.anweisungen(db, user)
        if hinweise:
            result["instructions"] = hinweise
        return _answer(id_, result)

    if methode == "tools/list":
        return _answer(id_, {"tools": await mail_mcp.werkzeugliste(db, user)})

    if methode == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            result = await mail_mcp.ausfuehren(db, user, name, args)
        except PermissionError as exc:
            # Eine Sperre ist kein Absturz: der Agent soll lesen können, warum es nicht geht,
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


# ── Token verwalten (aus der Oberfläche) ────────────────────────────────────

@router.post("/mailbox/mcp-token")
async def token_renew(user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """Erzeugt ein neues Token und zeigt es EINMAL. Ein altes wird damit ungültig."""
    roh = "trmcp_" + secrets.token_urlsafe(32)
    user.mail_mcp_token_enc = encrypt_secret(roh)
    await db.commit()
    return {"token": roh}


@router.get("/mailbox/mcp-status")
async def token_state(user: User = Depends(get_current_user)):
    """Nur ob eines existiert — das Token selbst kommt nie wieder heraus."""
    return {"token_set": bool(user.mail_mcp_token_enc),
            "fingerprint": (hashlib.sha256(user.mail_mcp_token_enc.encode()).hexdigest()[:8]
                            if user.mail_mcp_token_enc else "")}


@router.delete("/mailbox/mcp-token", status_code=204)
async def token_delete(user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    user.mail_mcp_token_enc = ""
    await db.commit()
