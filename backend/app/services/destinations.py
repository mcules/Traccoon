"""Ausgehende HTTP-Aufrufe über benannte Ziele.

Ein **Ziel** (`models/destination.Destination`) trägt Basis-URL und Anmeldung, der Aufruf
nennt nur Methode, Pfad-Ergänzung, Query, Header und Body. Genutzt von der Prozess-Aktion
`http_request`, von Jobs der Art `http` und vom Agenten-Werkzeug `http_call`.

Grundsätze:
- **Zugangsdaten verlassen den Dienst nicht.** Sie werden erst hier entschlüsselt, in den
  Request gesetzt und nie protokolliert oder zurückgegeben; `sanitize` schwärzt zusätzlich
  bekannte Kopfzeilen, bevor irgendetwas gespeichert wird.
- **Auflösung nach Namen** in fester Reihenfolge: Projekt → Nutzer → systemweit. Damit kann
  ein Projekt ein gleichnamiges Ziel auf eine Testgegenstelle umbiegen, ohne Prozesse zu
  ändern.
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
# Rückfall, wenn ein Ziel keine eigene Grenze trägt. Die maßgebliche Grenze steht seit
# ABC-31 am Ziel (`Destination.max_response_chars`).
MAX_RESPONSE_CHARS = 4000
# Zugriffstoken so lange vor dem echten Ablauf erneuern (Uhren-Drift, Laufzeit).
TOKEN_SKEW_SECONDS = 60

# Kopfzeilen, deren Wert nie in Ergebnis, Kontext oder Log gehört.
_SENSITIVE = {"authorization", "proxy-authorization", "cookie", "set-cookie"}


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def sanitize(headers: dict) -> dict:
    """Kopfzeilen für Ausgabe/Speicherung schwärzen (Auth, Cookies, konfigurierte Schlüssel)."""
    out = {}
    for k, v in (headers or {}).items():
        out[k] = "***" if k.lower() in _SENSITIVE else v
    return out


# ── Auflösung ────────────────────────────────────────────────────────────────

async def resolve(db: AsyncSession, name: str, *, project_id: int | None = None,
                  owner_id: int | None = None) -> Destination | None:
    """Ziel nach Namen: Projekt → Nutzer → systemweit. Nur aktivierte Ziele."""
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
    """Vorrang bei gleichem Namen: Projekt (0) vor Nutzer (1) vor systemweit (2)."""
    if d.project_id is not None:
        return 0
    return 1 if d.user_id is not None else 2


async def visible(db: AsyncSession, *, project_id: int | None = None,
                  owner_id: int | None = None, agents_only: bool = False) -> list[Destination]:
    """Alle Ziele, die in diesem Zusammenhang aufrufbar sind (je Name das vorrangige)."""
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
    """Zugriffstoken per Client Credentials holen — zwischengespeichert bis kurz vor Ablauf."""
    if dest.oauth_token_enc and dest.oauth_expires_at:
        rest = (dest.oauth_expires_at - _now()).total_seconds()
        if rest > TOKEN_SKEW_SECONDS:
            return decrypt_secret(dest.oauth_token_enc)
    if not (dest.oauth_token_url and dest.oauth_client_id):
        raise ValueError(f"Ziel '{dest.name}': OAuth2 ohne Token-URL/Client-ID")

    data = {"grant_type": "client_credentials"}
    if dest.oauth_scope:
        data["scope"] = dest.oauth_scope
    if dest.oauth_audience:
        data["audience"] = dest.oauth_audience
    secret = decrypt_secret(dest.secret_enc) if dest.secret_enc else ""
    async with httpx.AsyncClient(verify=dest.verify_tls) as client:
        resp = await client.post(
            dest.oauth_token_url, data=data,
            auth=(dest.oauth_client_id, secret),   # client_secret_basic (breit unterstützt)
            headers={"Accept": "application/json"}, timeout=dest.timeout_sec or 30)
    if resp.status_code >= 400:
        raise ValueError(f"Ziel '{dest.name}': Token-Abruf fehlgeschlagen "
                         f"({resp.status_code}: {resp.text[:200]})")
    payload = resp.json()
    token = payload.get("access_token") or ""
    if not token:
        raise ValueError(f"Ziel '{dest.name}': Antwort ohne access_token")
    ttl = int(payload.get("expires_in") or 3600)
    dest.oauth_token_enc = encrypt_secret(token)
    dest.oauth_expires_at = _now() + dt.timedelta(seconds=ttl)
    await db.commit()
    return token


async def _apply_auth(db: AsyncSession, dest: Destination, headers: dict, query: dict,
                      body_bytes: bytes) -> tuple[dict, dict, tuple[str, str] | None]:
    """Anmeldung auf Kopfzeilen/Query anwenden. Liefert (headers, query, basic-auth)."""
    auth = None
    secret = decrypt_secret(dest.secret_enc) if dest.secret_enc else ""
    kind = dest.auth_type or "none"

    if kind == "basic":
        auth = (dest.username, secret)
    elif kind == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif kind == "api_key":
        feld = dest.api_key_name or "X-API-Key"
        if (dest.api_key_in or "header") == "query":
            query[feld] = secret
        else:
            headers[feld] = secret
    elif kind == "hmac":
        algo = getattr(hashlib, (dest.hmac_algo or "sha256").lower(), hashlib.sha256)
        sig = hmac.new(secret.encode(), body_bytes, algo).hexdigest()
        headers[dest.hmac_header or "X-Webhook-Signature"] = f"{dest.hmac_prefix or ''}{sig}"
    elif kind == "oauth2_cc":
        headers["Authorization"] = f"Bearer {await _oauth_token(db, dest)}"
    return headers, query, auth


# ── Aufruf ───────────────────────────────────────────────────────────────────

def build_url(base_url: str, path: str = "", query: dict | None = None) -> str:
    """Basis-URL um den Pfad ergänzen und Query anhängen (vorhandene Query bleibt erhalten)."""
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
    """Ruft das Ziel auf und liefert ein aufbereitetes Ergebnis (ohne Geheimnisse).

    `body` darf dict/list (→ JSON) oder Text sein; bei GET/HEAD/DELETE/OPTIONS wird er
    weggelassen. Die Antwort wird als `json` geparst, wenn möglich, sonst als `text`.
    """
    verb = (method or "POST").upper()
    if verb not in METHODS:
        raise ValueError(f"Methode '{method}' wird nicht unterstützt")

    kopf = {**(dest.default_headers or {}), **(headers or {})}
    q = dict(query or {})

    daten: bytes | None = None
    if verb not in BODYLESS and body is not None:
        if isinstance(body, (dict, list)):
            daten = json.dumps(body).encode()
            kopf.setdefault("Content-Type", "application/json")
        else:
            daten = str(body).encode()
            kopf.setdefault("Content-Type", "text/plain; charset=utf-8")

    kopf, q, basic = await _apply_auth(db, dest, kopf, q, daten or b"")
    url = build_url(dest.base_url, path, q)

    async with httpx.AsyncClient(verify=dest.verify_tls, follow_redirects=True) as client:
        resp = await client.request(
            verb, url, headers=kopf, content=daten, auth=basic,
            timeout=timeout or dest.timeout_sec or 30)

    dest.last_used_at = _now()
    ergebnis: dict = {
        "destination": dest.name,
        "method": verb,
        # Ohne Query: dort könnte ein API-Key stehen (api_key_in=query).
        "url": build_url(dest.base_url, path),
        "status_code": resp.status_code,
        "ok": 200 <= resp.status_code < 300,
    }
    text = resp.text or ""
    # Die Grenze kommt vom Ziel (ABC-31). Sie steht auch im Ergebnis, damit der Aufrufer
    # nicht ein zweites Mal kürzt und dabei die Erlaubnis des Ziels wieder einkassiert.
    grenze = dest.max_response_chars or MAX_RESPONSE_CHARS
    ergebnis["max_chars"] = grenze
    try:
        ergebnis["json"] = resp.json()
    except Exception:  # noqa: BLE001 — kein JSON: Text reicht
        ergebnis["text"] = text[:grenze]
    else:
        if len(text) <= grenze:
            ergebnis["text"] = text
    if not ergebnis["ok"]:
        ergebnis["error"] = text[:500] or f"HTTP {resp.status_code}"
    log.info("Ziel %s: %s %s → %s", dest.name, verb, urlsplit(ergebnis["url"]).path or "/",
             resp.status_code)
    return ergebnis


async def call_by_name(db: AsyncSession, name: str, *, project_id: int | None = None,
                       owner_id: int | None = None, agents_only: bool = False,
                       **kwargs) -> dict:
    """Bequemer Einstieg: Ziel auflösen und aufrufen."""
    dest = await resolve(db, name, project_id=project_id, owner_id=owner_id)
    if dest is None:
        raise ValueError(f"Unbekanntes oder deaktiviertes Ziel '{name}'")
    if agents_only and not dest.allow_agents:
        raise ValueError(f"Ziel '{name}' ist nicht für KI-Agenten freigegeben")
    return await call(db, dest, **kwargs)
