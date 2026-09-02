"""The note workspace, reached through this house.

The workspace itself is still the separate service it grew up as. This module is
the bridge: the browser talks only to Traccoon, Traccoon passes the request on
and says who is asking. That is what makes the notes an area of this application
instead of a second application with a second login, and it holds while the
workspace is being rewritten behind it.

Two things are worth knowing before changing anything here.

**How the far side learns who is calling.** It trusts a `Remote-User` header,
the same way it trusted the single sign on in front of it. That is only safe
because it publishes no port of its own and nobody but this bridge can reach it.
Whoever exposes that service directly has to take the trust away first.

**Why there is a cookie at all.** A picture inside a note is an `<img src>`, and
the browser sends no `Authorization` header for those. The session token lives in
the memory of the application, so it cannot travel that way either. So the notes
area asks for a cookie once and uses it for reading files, and for nothing else:
it is refused for every method except GET, which is what keeps a foreign page
from writing into the vault with it.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.error import Error
from ..db import get_session
from ..models.enums import UserStatus
from ..models.user import User
from .deps import get_current_user

router = APIRouter(prefix="/notes", tags=["notes"])

ASSET_COOKIE = "notes_asset"
ASSET_TTL = 12 * 3600

# Headers that describe the request and have to survive the hop. Everything else
# is about this connection (host, length, encoding) and would be a lie on the
# next one.
FORWARD_REQUEST = {
    "content-type", "accept", "range", "if-match", "if-none-match",
    "if-modified-since", "x-client-id",
}
FORWARD_RESPONSE = {
    "content-type", "content-length", "content-range", "accept-ranges",
    "etag", "last-modified", "cache-control", "content-disposition",
}


def _asset_token(user_id: int) -> str:
    """A short lived ticket for reading files, deliberately not a JWT.

    A JWT here would be a second thing that looks like a session, and the day
    somebody feeds it to the session check is the day this becomes a hole. This
    is a signed string with one meaning and no parser worth attacking.
    """
    exp = int(time.time()) + ASSET_TTL
    body = f"{user_id}.{exp}"
    sig = hmac.new(settings.jwt_secret.encode(), f"notes-asset:{body}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _asset_user_id(token: str) -> int | None:
    try:
        uid_s, exp_s, sig = token.split(".", 2)
        body = f"{uid_s}.{exp_s}"
        want = hmac.new(settings.jwt_secret.encode(), f"notes-asset:{body}".encode(),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return int(uid_s)
    except (ValueError, AttributeError):
        return None


async def notes_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Who is asking: the normal token, or the reading cookie for a GET."""
    if authorization:
        return await get_current_user(request, authorization, db)
    if request.method == "GET":
        uid = _asset_user_id(request.cookies.get(ASSET_COOKIE) or "")
        if uid is not None:
            user = await db.get(User, uid)
            if user is not None and user.status == UserStatus.active:
                request.state.scopes = None
                return user
    raise Error(status.HTTP_401_UNAUTHORIZED, "err.not_authenticated", "Not authenticated")


def _vault_of(user: User) -> str:
    path = (user.vault_path or "").strip()
    if not path:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_no_vault",
                    "This account has no note vault")
    return path


@router.post("/session")
async def open_session(response: Response, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Hand out the reading cookie. The notes page asks for this when it opens."""
    _vault_of(user)
    response.set_cookie(
        ASSET_COOKIE, _asset_token(user.id),
        max_age=ASSET_TTL, httponly=True, samesite="lax", secure=True,
        # Narrow on purpose: it is worth nothing anywhere else in the house.
        path="/api/notes",
    )
    return {"ok": True}


@router.post("/session/end")
async def close_session(response: Response, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    response.delete_cookie(ASSET_COOKIE, path="/api/notes")
    return {"ok": True}


@router.api_route("/{path:path}",
                  methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def proxy(path: str, request: Request, user: User = Depends(notes_user)):
    """Pass the request on and stream the answer back."""
    _vault_of(user)
    if not settings.notes_base_url:
        raise Error(status.HTTP_503_SERVICE_UNAVAILABLE, "err.notes_unconfigured",
                    "The note workspace is not configured")

    url = f"{settings.notes_base_url.rstrip('/')}/api/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQUEST}
    # Who is asking. Set here and nowhere else, and never taken from the caller:
    # a client that sends its own would otherwise pick its own identity.
    headers["Remote-User"] = user.username or user.email or str(user.id)

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0))
    req = client.build_request(
        request.method, url, headers=headers,
        params=dict(request.query_params),
        content=await request.body() if request.method not in ("GET", "HEAD") else None,
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise Error(status.HTTP_502_BAD_GATEWAY, "err.notes_unreachable",
                    "The note workspace does not answer: {reason}", reason=str(exc)) from exc

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body(), status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() in FORWARD_RESPONSE},
    )
