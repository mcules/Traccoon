"""Plugin system: a zip in the database, serving it, generic tables (JSON against a schema
whitelist), an SSRF-protected fetch proxy — and the rights under which a plugin may see
Traccoon data.

A plugin runs in the browser in an iframe **without** `allow-same-origin`. It therefore has
an opaque origin and reaches neither the token in `localStorage` nor the API. Whatever data
it needs it asks the host for (`frontend/src/pages/PluginHost.tsx`), and the host hands out
only what the manifest declared (`reads`) and a human granted (`reads_granted`). Deny by
default, as with the tools of the agents: a manifest may ask for anything, only an admin
grants it.
"""
from __future__ import annotations

import io
import ipaddress
import json
import socket
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Fehler
from ..db import get_session
from ..models.plugins import Plugin, PluginData, PluginFile
from ..models.user import User
from .deps import get_current_user, require_admin

router = APIRouter(prefix="/plugins", tags=["plugins"])
MAX_UNZIP = 25 * 1024 * 1024


@router.get("")
async def list_plugins(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Plugin))).scalars().all()
    out = []
    for p in rows:
        visible = p.enabled and (p.all_users or user.id in (p.allowed_user_ids or [])
                                 or user.global_role.value == "admin")
        if visible:
            out.append({"slug": p.slug, "name": p.name, "version": p.version, "icon": p.icon,
                        "entry": p.entry, "contributions": p.contributions,
                        # Der Wirt braucht die Freigaben, um jeden Ruf des Plugins daran zu
                        # messen. `liest` steht daneben, damit die Oberflaeche zeigen kann,
                        # was ein Plugin verlangt und noch nicht bekommen hat.
                        "reads": p.reads or [], "reads_granted": p.reads_granted or []})
    return out


@router.get("/alle")
async def list_all(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """Alles, auch Abgeschaltetes — die Sicht der Verwaltung."""
    rows = (await db.execute(select(Plugin))).scalars().all()
    return [{"slug": p.slug, "name": p.name, "version": p.version, "icon": p.icon,
             "description": p.description, "entry": p.entry, "enabled": p.enabled,
             "all_users": p.all_users, "allowed_user_ids": p.allowed_user_ids or [],
             "contributions": p.contributions or [], "reads": p.reads or [],
             "reads_granted": p.reads_granted or [], "csp": p.csp or {},
             "allowed_hosts": p.allowed_hosts or []} for p in rows]


@router.post("", status_code=201)
async def upload_plugin(file: UploadFile, _: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise Fehler(400, "err.not_valid_zip_file", "Not a valid zip file")
    # Manifest finden (flachstes gewinnt)
    manifests = [n for n in zf.namelist() if n.endswith("manifest.json")]
    if not manifests:
        raise Fehler(400, "err.manifest_json_missing", "manifest.json is missing")
    manifests.sort(key=lambda n: n.count("/"))
    base = manifests[0].rsplit("manifest.json", 1)[0]
    try:
        manifest = json.loads(zf.read(manifests[0]))
    except json.JSONDecodeError:
        raise Fehler(400, "err.manifest_json_invalid", "manifest.json is invalid")
    slug = manifest.get("slug") or manifest.get("id")
    if not slug or not all(c.isalnum() or c in "-_" for c in slug):
        raise Fehler(400, "err.invalid_slug", "invalid slug")

    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_UNZIP:
        raise Fehler(400, "err.plugin_too_large_mb", "Plugin too large (>25MB)")

    plugin = (await db.execute(select(Plugin).where(Plugin.slug == slug))).scalar_one_or_none()
    if plugin is None:
        plugin = Plugin(slug=slug)
        db.add(plugin)
    plugin.name = manifest.get("name", slug)
    plugin.version = str(manifest.get("version", "0.1.0"))
    plugin.description = manifest.get("description", "")
    plugin.icon = manifest.get("icon", "")
    plugin.entry = manifest.get("entry", "index.html")
    plugin.table_schema = manifest.get("table_schema", {})
    plugin.allowed_hosts = manifest.get("allowed_hosts", [])
    plugin.contributions = manifest.get("contributions", [])
    plugin.csp = manifest.get("csp", {}) or {}
    gefordert = [str(r) for r in (manifest.get("reads") or [])]
    # Eine neue Fassung darf sich nicht selbst mehr erlauben: Bestehende Freigaben bleiben,
    # aber nur solange sie noch gefordert werden. Alles Neue faengt bei "nicht erlaubt" an
    # und braucht wieder einen Menschen.
    plugin.reads_granted = [r for r in (plugin.reads_granted or []) if r in gefordert]
    plugin.reads = gefordert
    await db.flush()
    # Alte Dateien ersetzen. Das `flush` danach ist nicht kosmetisch: Ohne es fuehrt
    # SQLAlchemy erst die neuen INSERTs und dann die DELETEs aus, und der eindeutige Index
    # (plugin_id, path) schlaegt zu — eine neue Fassung eines Plugins liess sich damit gar
    # nicht einspielen.
    await db.execute(sa_delete(PluginFile).where(PluginFile.plugin_id == plugin.id))
    await db.flush()
    for name in zf.namelist():
        if name.endswith("/") or "/../" in name or name.startswith("/"):
            continue
        if not name.startswith(base):
            continue
        rel = name[len(base):]
        if not rel:
            continue
        data = zf.read(name)
        db.add(PluginFile(plugin_id=plugin.id, path=rel, data=data, size=len(data),
                          content_type=_ctype(rel)))
    await db.commit()
    return {"slug": slug, "files": len(zf.namelist())}


# Der Dateityp muss stimmen: Ausgeliefert wird mit `nosniff`, ein Stylesheet als
# `octet-stream` wuerde der Browser gar nicht erst anwenden.
TYPEN = {
    ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript",
    ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ttf": "font/ttf", ".map": "application/json",
    ".txt": "text/plain", ".md": "text/markdown",
}


def _ctype(path: str) -> str:
    punkt = path.rfind(".")
    return TYPEN.get(path[punkt:].lower(), "application/octet-stream") if punkt >= 0 \
        else "application/octet-stream"


# Was ein Plugin an fremden Quellen nachladen darf, ist eine kurze Liste — mehr Richtungen
# aufzumachen hiesse, Loecher auf Vorrat zu bohren.
CSP_DIRECTIONS = ("img-src", "style-src", "font-src", "media-src")


def _herkunft(request: Request) -> str:
    """Die eigene Adresse fuer die CSP — als Rechnername mit offenem Port.

    Sie muss ausgeschrieben dort stehen: Ein iframe ohne `allow-same-origin` hat die Herkunft
    `null`, und `'self'` zeigt dann ins Leere — die eigenen Dateien des Plugins waeren als
    erstes gesperrt.

    Ausgeschrieben wird bewusst **nur der Rechnername**, mit `*` als Port und ohne Schema.
    Der Grund ist, dass der Server die Adresse, die der Browser benutzt hat, gar nicht sicher
    kennt: Der nginx davor reicht `Host` ohne Port weiter und setzt keine `X-Forwarded-`
    Kopfzeilen, Traefik in der anderen Richtung setzt sie schon. Ein geratener Port sperrt
    dann genau die Dateien aus, um die es geht. Ein offener Port auf demselben Rechnernamen
    erlaubt nichts, was ein Plugin nicht ohnehin haette — es liegt selbst dort.
    """
    host = (request.headers.get("x-forwarded-host", "").split(",")[0].strip()
            or request.headers.get("host", "").strip()
            or request.url.netloc)
    # Port abschneiden, IPv6 in Klammern beachten (`[::1]:8800`).
    if host.startswith("["):
        host = host.split("]")[0] + "]"
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    return f"{host}:*" if host else "'self'"


def _csp(request: Request, plugin: Plugin) -> str:
    """Der Zaun um eine Plugin-Seite.

    `connect-src 'none'` ist der Kern: Ein Plugin kann von sich aus gar nicht ins Netz — weder
    zu Traccoon noch nach draussen. Daten bekommt es ueber die Bruecke zum Wirt, fremde Dienste
    ueber den `allowed_hosts`-Proxy. Was es darueber hinaus laden darf (Kacheln einer Karte
    etwa), steht im Manifest und nur dort.
    """
    ich = _herkunft(request)
    extra = plugin.csp or {}
    parts = [
        "default-src 'none'",
        f"script-src {ich} 'unsafe-inline'",
        f"style-src {ich} 'unsafe-inline'",
        "connect-src 'none'",
        "frame-ancestors *",
    ]
    for richtung in CSP_DIRECTIONS:
        quellen = [q for q in (extra.get(richtung) or []) if isinstance(q, str) and " " not in q]
        if richtung == "img-src":
            parts.append(" ".join([f"img-src {ich}", "data:", *quellen]))
        elif quellen:
            parts.append(" ".join([f"{richtung} {ich}", *quellen]))
    return "; ".join(parts)


@router.get("/{slug}/app/{path:path}")
async def serve_file(slug: str, path: str, request: Request,
                     db: AsyncSession = Depends(get_session)):
    """Eine Datei des Plugins.

    Bewusst ohne Anmeldung: Das iframe hat keine Herkunft und schickt daher weder Cookie noch
    Token mit. Ausgeliefert wird ohnehin nur der Code des Plugins, keine Daten — die gibt es
    ausschliesslich ueber die Bruecke, und die haengt am angemeldeten Menschen.
    """
    plugin = (await db.execute(select(Plugin).where(Plugin.slug == slug))).scalar_one_or_none()
    if plugin is None:
        raise Fehler(404, "err.plugin_not_found", "Plugin not found")
    if not plugin.enabled:
        raise Fehler(404, "err.plugin_not_found", "Plugin not found")
    f = (await db.execute(select(PluginFile).where(PluginFile.plugin_id == plugin.id,
                                                   PluginFile.path == (path or plugin.entry)))).scalar_one_or_none()
    if f is None:
        raise Fehler(404, "err.file_not_found", "File not found")
    return Response(content=f.data, media_type=f.content_type, headers={
        "Cache-Control": "no-cache",
        "Content-Security-Policy": _csp(request, plugin),
        "X-Content-Type-Options": "nosniff",
    })


class RechteIn(BaseModel):
    """Was ein Mensch an einem Plugin entscheidet."""
    reads_granted: list[str] | None = None
    enabled: bool | None = None
    all_users: bool | None = None
    allowed_user_ids: list[int] | None = None


@router.put("/{slug}/rechte")
async def set_rechte(slug: str, data: RechteIn, _: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_session)):
    """Freigaben und Sichtbarkeit setzen.

    Erlauben laesst sich nur, was das Manifest auch angemeldet hat. Ein Plugin koennte sonst
    ueber diesen Weg an Rechte kommen, die niemand an ihm gelesen hat.
    """
    plugin = await _plugin(db, slug)
    if data.reads_granted is not None:
        gefordert = set(plugin.reads or [])
        unbekannt = [r for r in data.reads_granted if r not in gefordert]
        if unbekannt:
            raise Fehler(400, "err.right_not_requested",
                         "The plugin does not ask for the right '{recht}'", recht=unbekannt[0])
        plugin.reads_granted = list(data.reads_granted)
    if data.enabled is not None:
        plugin.enabled = data.enabled
    if data.all_users is not None:
        plugin.all_users = data.all_users
    if data.allowed_user_ids is not None:
        plugin.allowed_user_ids = list(data.allowed_user_ids)
    await db.commit()
    return {"slug": plugin.slug, "enabled": plugin.enabled, "all_users": plugin.all_users,
            "allowed_user_ids": plugin.allowed_user_ids or [],
            "reads_granted": plugin.reads_granted or []}


@router.get("/_bridge.js")
async def bridge_js():
    """Das Stueck JavaScript, das ein Plugin als `traccoon` einbindet.

    Es kapselt nur das Hin und Her mit dem Wirt. Absichtlich eine ausgelieferte Datei und
    keine Kopie im Zip jedes Plugins: Sonst traegt jedes Plugin seinen eigenen, irgendwann
    veralteten Stand der Bruecke mit sich herum.
    """
    file = Path(__file__).resolve().parent.parent / "static" / "plugin_bridge.js"
    return Response(content=file.read_bytes(), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@router.delete("/{slug}", status_code=204)
async def delete_plugin(slug: str, _: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    p = (await db.execute(select(Plugin).where(Plugin.slug == slug))).scalar_one_or_none()
    if p:
        await db.delete(p)
        await db.commit()


# ---------------- Generische Table-CRUD (JSON, Schema-Whitelist) ----------------

async def _plugin(db: AsyncSession, slug: str) -> Plugin:
    p = (await db.execute(select(Plugin).where(Plugin.slug == slug))).scalar_one_or_none()
    if p is None:
        raise Fehler(404, "err.plugin_not_found", "Plugin not found")
    return p


def _validate_row(plugin: Plugin, table: str, row: dict) -> dict:
    schema = (plugin.table_schema or {}).get(table)
    if schema is None:
        raise Fehler(400, "err.table_not_plugin_schema",
                     "The table '{name}' is not in the plugin schema", name=table)
    allowed = set(schema.keys())
    return {k: v for k, v in row.items() if k in allowed}


@router.get("/{slug}/data/{table}")
async def data_list(slug: str, table: str, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    plugin = await _plugin(db, slug)
    _validate_row(plugin, table, {})  # checks that the table exists
    rows = (await db.execute(select(PluginData).where(
        PluginData.plugin_id == plugin.id, PluginData.table_name == table,
        (PluginData.user_id == user.id) | (PluginData.user_id.is_(None))))).scalars().all()
    return [{"id": r.id, **r.row} for r in rows]


@router.post("/{slug}/data/{table}", status_code=201)
async def data_create(slug: str, table: str, row: dict, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    plugin = await _plugin(db, slug)
    clean = _validate_row(plugin, table, row)
    pd = PluginData(plugin_id=plugin.id, table_name=table, user_id=user.id, row=clean)
    db.add(pd)
    await db.commit()
    await db.refresh(pd)
    return {"id": pd.id, **pd.row}


@router.delete("/{slug}/data/{table}/{rid}", status_code=204)
async def data_delete(slug: str, table: str, rid: int, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    pd = await db.get(PluginData, rid)
    if pd and (pd.user_id == user.id or user.global_role.value == "admin"):
        await db.delete(pd)
        await db.commit()


# ---------------- Fetch-Proxy (SSRF-Schutz) ----------------

class FetchIn(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}
    body: str | None = None


def _ssrf_ok(url: str, allowed_hosts: list[str]) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    # An empty allowed_hosts means nothing is allowed (no open proxy). The whitelist is mandatory.
    if not allowed_hosts or parts.hostname not in allowed_hosts:
        return False
    try:
        for res in socket.getaddrinfo(parts.hostname, None):
            ip = ipaddress.ip_address(res[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


@router.post("/{slug}/fetch")
async def fetch_proxy(slug: str, data: FetchIn, _: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    plugin = await _plugin(db, slug)
    if not _ssrf_ok(data.url, plugin.allowed_hosts or []):
        raise Fehler(400, "err.url_not_allowed",
                     "URL not allowed (SSRF protection / allowed_hosts)")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            r = await client.request(data.method, data.url, headers=data.headers, content=data.body)
        return {"status": r.status_code, "body": r.text[:5 * 1024 * 1024]}
    except Exception as exc:  # noqa: BLE001
        raise Fehler(502, "err.fetch_error", "Fetch error: {reason}", reason=exc)
