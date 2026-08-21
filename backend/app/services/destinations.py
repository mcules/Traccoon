"""Outgoing HTTP calls over named destinations.

A **destination** (`models/destination.Destination`) carries the base URL and the login; the
call only names the method, the path addition, query, headers and body. Used by the process
action `http_request`, by jobs of kind `http` and by the agent tool `http_call`.

Principles:
- **Credentials do not leave the service.** They are decrypted only here, put into the
  request and never logged or returned; `sanitize` additionally redacts known headers before
  anything is stored.
- **Resolution by name** in a fixed order: project, user, system wide. That lets a project
  bend a destination of the same name onto a test counterpart without changing processes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decrypt_secret, encrypt_secret
from ..models.destination import Destination

log = logging.getLogger("destinations")

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
BODYLESS = ("GET", "HEAD", "DELETE", "OPTIONS")
AUTH_TYPES = ("none", "basic", "bearer", "api_key", "hmac", "oauth2_cc")
# Fallback when a destination carries no limit of its own. The authoritative limit has stood
# on the destination since TRA-31 (`Destination.max_response_chars`).
MAX_RESPONSE_CHARS = 4000
# Renew the access token this long before the real expiry (clock drift, runtime).
TOKEN_SKEW_SECONDS = 60

# Headers whose value never belongs in a result, a context or a log.
_SENSITIVE = {"authorization", "proxy-authorization", "cookie", "set-cookie"}


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def sanitize(headers: dict) -> dict:
    """Redact headers for output or storage (auth, cookies, configured keys)."""
    out = {}
    for k, v in (headers or {}).items():
        out[k] = "***" if k.lower() in _SENSITIVE else v
    return out


# ── Resolution ───────────────────────────────────────────────────────────────

async def resolve(db: AsyncSession, name: str, *, project_id: int | None = None,
                  owner_id: int | None = None) -> Destination | None:
    """Destination by name: project, user, system wide. Only enabled destinations."""
    if not name:
        return None
    bereiche = [Destination.user_id.is_(None) & Destination.project_id.is_(None)]
    if owner_id:
        bereiche.append((Destination.user_id == owner_id) & Destination.project_id.is_(None))
    if project_id:
        bereiche.append(Destination.project_id == project_id)
    rows = (await db.execute(
        select(Destination).where(
            Destination.name == name, Destination.enabled.is_(True), or_(*bereiche),
        ))).scalars().all()
    return sorted(rows, key=_rang)[0] if rows else None


def _rang(d: Destination) -> int:
    """Precedence with the same name: project (0) before user (1) before system wide (2)."""
    if d.project_id is not None:
        return 0
    return 1 if d.user_id is not None else 2


async def visible(db: AsyncSession, *, project_id: int | None = None,
                  owner_id: int | None = None, agents_only: bool = False) -> list[Destination]:
    """All destinations callable in this context (the primary one per name)."""
    rows = (await db.execute(select(Destination).order_by(Destination.name))).scalars().all()
    passend = [
        d for d in rows
        if d.enabled
        and (d.project_id == project_id if d.project_id is not None
             else (d.user_id in (None, owner_id)))
        and (d.allow_agents or not agents_only)
    ]
    beste: dict[str, Destination] = {}
    for d in passend:
        cur = beste.get(d.name)
        if cur is None or _rang(d) < _rang(cur):
            beste[d.name] = d
    return sorted(beste.values(), key=lambda d: d.name)


# ── Authentifizierung ────────────────────────────────────────────────────────

async def _oauth_token(db: AsyncSession, dest: Destination) -> str:
    """Fetch an access token over client credentials, cached until shortly before expiry."""
    if dest.oauth_token_enc and dest.oauth_expires_at:
        remainder = (dest.oauth_expires_at - _now()).total_seconds()
        if remainder > TOKEN_SKEW_SECONDS:
            return decrypt_secret(dest.oauth_token_enc)
    if not (dest.oauth_token_url and dest.oauth_client_id):
        raise ValueError(f"Destination '{dest.name}': OAuth2 without a token URL or client id")

    data = {"grant_type": "client_credentials"}
    if dest.oauth_scope:
        data["scope"] = dest.oauth_scope
    if dest.oauth_audience:
        data["audience"] = dest.oauth_audience
    secret = decrypt_secret(dest.secret_enc) if dest.secret_enc else ""
    async with httpx.AsyncClient(verify=dest.verify_tls) as client:
        resp = await client.post(
            dest.oauth_token_url, data=data,
            auth=(dest.oauth_client_id, secret),   # client_secret_basic (widely supported)
            headers={"Accept": "application/json"}, timeout=dest.timeout_sec or 30)
    if resp.status_code >= 400:
        raise ValueError(f"Destination '{dest.name}': fetching the token failed "
                         f"({resp.status_code}: {resp.text[:200]})")
    payload = resp.json()
    token = payload.get("access_token") or ""
    if not token:
        raise ValueError(f"Destination '{dest.name}': answer without an access_token")
    ttl = int(payload.get("expires_in") or 3600)
    dest.oauth_token_enc = encrypt_secret(token)
    dest.oauth_expires_at = _now() + dt.timedelta(seconds=ttl)
    await db.commit()
    return token


async def _apply_auth(db: AsyncSession, dest: Destination, headers: dict, query: dict,
                      body_bytes: bytes) -> tuple[dict, dict, tuple[str, str] | None]:
    """Apply the login to headers and query. Returns (headers, query, basic auth)."""
    auth = None
    secret = decrypt_secret(dest.secret_enc) if dest.secret_enc else ""
    kind = dest.auth_type or "none"

    if kind == "basic":
        auth = (dest.username, secret)
    elif kind == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif kind == "api_key":
        field = dest.api_key_name or "X-API-Key"
        if (dest.api_key_in or "header") == "query":
            query[field] = secret
        else:
            headers[field] = secret
    elif kind == "hmac":
        algo = getattr(hashlib, (dest.hmac_algo or "sha256").lower(), hashlib.sha256)
        sig = hmac.new(secret.encode(), body_bytes, algo).hexdigest()
        headers[dest.hmac_header or "X-Webhook-Signature"] = f"{dest.hmac_prefix or ''}{sig}"
    elif kind == "oauth2_cc":
        headers["Authorization"] = f"Bearer {await _oauth_token(db, dest)}"
    return headers, query, auth


# ── Aufruf ───────────────────────────────────────────────────────────────────

def build_url(base_url: str, path: str = "", query: dict | None = None) -> str:
    """Extend the base URL by the path and append the query (an existing query is kept)."""
    base = (base_url or "").rstrip("/")
    p = (path or "").strip()
    if p and not p.startswith("?"):
        base = f"{base}/{p.lstrip('/')}"
    elif p:
        base = f"{base}{p}"
    if query:
        sauber = {k: v for k, v in query.items() if v is not None}
        if sauber:
            trenner = "&" if "?" in base else "?"
            base = f"{base}{trenner}{urlencode(sauber, doseq=True)}"
    return base


async def call(db: AsyncSession, dest: Destination, *, method: str = "POST", path: str = "",
               query: dict | None = None, headers: dict | None = None, body=None,
               timeout: int | None = None) -> dict:
    """Calls the destination and delivers a prepared result (without secrets).

    `body` may be a dict or list (becoming JSON) or text; with GET/HEAD/DELETE/OPTIONS it is
    left out. The answer is parsed as `json` when possible, otherwise as `text`.
    """
    verb = (method or "POST").upper()
    if verb not in METHODS:
        raise ValueError(f"The method '{method}' is not supported")

    header = {**(dest.default_headers or {}), **(headers or {})}
    q = dict(query or {})

    daten: bytes | None = None
    if verb not in BODYLESS and body is not None:
        if isinstance(body, (dict, list)):
            daten = json.dumps(body).encode()
            header.setdefault("Content-Type", "application/json")
        else:
            daten = str(body).encode()
            header.setdefault("Content-Type", "text/plain; charset=utf-8")

    header, q, basic = await _apply_auth(db, dest, header, q, daten or b"")
    url = build_url(dest.base_url, path, q)

    async with httpx.AsyncClient(verify=dest.verify_tls, follow_redirects=True) as client:
        resp = await client.request(
            verb, url, headers=header, content=daten, auth=basic,
            timeout=timeout or dest.timeout_sec or 30)

    dest.last_used_at = _now()
    result: dict = {
        "destination": dest.name,
        "method": verb,
        # Without the query: an API key could stand there (api_key_in=query).
        "url": build_url(dest.base_url, path),
        "status_code": resp.status_code,
        "ok": 200 <= resp.status_code < 300,
    }
    text = resp.text or ""
    # The limit comes from the destination (TRA-31). It stands in the result as well, so that
    # the caller does not truncate a second time and thereby revoke the permission again.
    limit = dest.max_response_chars or MAX_RESPONSE_CHARS
    result["max_chars"] = limit
    try:
        result["json"] = resp.json()
    except Exception:  # noqa: BLE001 - no JSON: text is enough
        result["text"] = text[:limit]
    else:
        if len(text) <= limit:
            result["text"] = text
    if not result["ok"]:
        result["error"] = text[:500] or f"HTTP {resp.status_code}"
    log.info("Destination %s: %s %s -> %s", dest.name, verb, urlsplit(result["url"]).path or "/",
             resp.status_code)
    return result


async def call_by_name(db: AsyncSession, name: str, *, project_id: int | None = None,
                       owner_id: int | None = None, agents_only: bool = False,
                       **kwargs) -> dict:
    """The comfortable entry: resolve the destination and call it."""
    dest = await resolve(db, name, project_id=project_id, owner_id=owner_id)
    if dest is None:
        raise ValueError(f"Unknown or disabled destination '{name}'")
    if agents_only and not dest.allow_agents:
        raise ValueError(f"The destination '{name}' is not released for AI agents")
    return await call(db, dest, **kwargs)
