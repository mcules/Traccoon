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

from ..core.error import Error
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
                        # The host needs the grants to measure every call of the plugin
                        # against them. `reads` stands next to them so the UI can show what a
                        # plugin demands and has not been given yet.
                        "reads": p.reads or [], "reads_granted": p.reads_granted or []})
    return out


@router.get("/all")
async def list_all(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """Everything, disabled ones included — the administrator's view."""
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
        raise Error(400, "err.not_valid_zip_file", "Not a valid zip file")
    # Manifest finden (flachstes gewinnt)
    manifests = [n for n in zf.namelist() if n.endswith("manifest.json")]
    if not manifests:
        raise Error(400, "err.manifest_json_missing", "manifest.json is missing")
    manifests.sort(key=lambda n: n.count("/"))
    base = manifests[0].rsplit("manifest.json", 1)[0]
    try:
        manifest = json.loads(zf.read(manifests[0]))
    except json.JSONDecodeError:
        raise Error(400, "err.manifest_json_invalid", "manifest.json is invalid")
    slug = manifest.get("slug") or manifest.get("id")
    if not slug or not all(c.isalnum() or c in "-_" for c in slug):
        raise Error(400, "err.invalid_slug", "invalid slug")

    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_UNZIP:
        raise Error(400, "err.plugin_too_large_mb", "Plugin too large (>25MB)")

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
    requested = [str(r) for r in (manifest.get("reads") or [])]
    # A new version must not grant itself more: existing grants stay, but only as long as they
    # are still being demanded. Everything new starts at "not allowed" and needs a person
    # again.
    plugin.reads_granted = [r for r in (plugin.reads_granted or []) if r in requested]
    plugin.reads = requested
    await db.flush()
    # Replace the old files. The `flush` afterwards is not cosmetic: without it SQLAlchemy
    # runs the new INSERTs first and the DELETEs afterwards, and the unique index
    # (plugin_id, path) fires — a new version of a plugin could not be installed at all
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


# The content type has to be right: delivery happens with `nosniff`, and a stylesheet as
# `octet-stream` is something the browser would not even apply.
TYPES = {
    ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript",
    ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ttf": "font/ttf", ".map": "application/json",
    ".txt": "text/plain", ".md": "text/markdown",
}


def _ctype(path: str) -> str:
    point = path.rfind(".")
    return TYPES.get(path[point:].lower(), "application/octet-stream") if point >= 0 \
        else "application/octet-stream"


# What a plugin may load from foreign sources is a short list — opening more directions
# aufzumachen hiesse, Loecher auf Vorrat zu bohren.
CSP_DIRECTIONS = ("img-src", "style-src", "font-src", "media-src")


def _origin(request: Request) -> str:
    """Our own address for the CSP — as a hostname with an open port.

    It has to stand there spelled out: an iframe without `allow-same-origin` has the origin
    `null`, and `'self'` then points into the void — the plugin's own files would be locked
    erstes gesperrt.

    Deliberately only the **hostname** is spelled out, with `*` as the port and without a
    scheme. The reason is that the server does not reliably know the address the browser used:
    the nginx in front passes `Host` on without the port and sets no `X-Forwarded-` headers,
    while Traefik in the other direction does set them. A guessed port then locks out exactly
    the files this is about. An open port on the same hostname allows nothing a plugin would
    not have anyway — it lies there itself.
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
    """The fence around a plugin page.

    `connect-src 'none'` is the core: a plugin cannot reach the network by itself — neither
    Traccoon nor the outside. Data reaches it through the bridge to the host, foreign services
    through the `allowed_hosts` proxy. What it may load beyond that (map tiles, say) stands in
    the manifest and only there.
    """
    me = _origin(request)
    extra = plugin.csp or {}
    parts = [
        "default-src 'none'",
        f"script-src {me} 'unsafe-inline'",
        f"style-src {me} 'unsafe-inline'",
        "connect-src 'none'",
        "frame-ancestors *",
    ]
    for direction in CSP_DIRECTIONS:
        sources = [q for q in (extra.get(direction) or []) if isinstance(q, str) and " " not in q]
        if direction == "img-src":
            parts.append(" ".join([f"img-src {me}", "data:", *sources]))
        elif sources:
            parts.append(" ".join([f"{direction} {me}", *sources]))
    return "; ".join(parts)


@router.get("/{slug}/app/{path:path}")
async def serve_file(slug: str, path: str, request: Request,
                     db: AsyncSession = Depends(get_session)):
    """Eine Datei des Plugins.

    Deliberately without a login: the iframe has no origin and therefore sends neither cookie
    nor token. Only the code of the plugin is delivered anyway, no data — that exists solely
    through the bridge, and the bridge hangs on the logged-in person.
    """
    plugin = (await db.execute(select(Plugin).where(Plugin.slug == slug))).scalar_one_or_none()
    if plugin is None:
        raise Error(404, "err.plugin_not_found", "Plugin not found")
    if not plugin.enabled:
        raise Error(404, "err.plugin_not_found", "Plugin not found")
    f = (await db.execute(select(PluginFile).where(PluginFile.plugin_id == plugin.id,
                                                   PluginFile.path == (path or plugin.entry)))).scalar_one_or_none()
    if f is None:
        raise Error(404, "err.file_not_found", "File not found")
    return Response(content=f.data, media_type=f.content_type, headers={
        "Cache-Control": "no-cache",
        "Content-Security-Policy": _csp(request, plugin),
        "X-Content-Type-Options": "nosniff",
    })


class RightsIn(BaseModel):
    """What a person decides about a plugin."""
    reads_granted: list[str] | None = None
    enabled: bool | None = None
    all_users: bool | None = None
    allowed_user_ids: list[int] | None = None


@router.put("/{slug}/rights")
async def set_rights(slug: str, data: RightsIn, _: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_session)):
    """Freigaben und Sichtbarkeit setzen.

    Only what the manifest has declared can be allowed. A plugin could otherwise obtain rights
    through this path that nobody read on it.
    """
    plugin = await _plugin(db, slug)
    if data.reads_granted is not None:
        requested = set(plugin.reads or [])
        unknown = [r for r in data.reads_granted if r not in requested]
        if unknown:
            raise Error(400, "err.right_not_requested",
                         "The plugin does not ask for the right '{right}'", right=unknown[0])
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
    """The piece of JavaScript a plugin includes as `traccoon`.

    It only wraps the back and forth with the host. Deliberately a delivered file and not a
    copy in the zip of every plugin: otherwise every plugin would carry its own, eventually
    outdated state of the bridge around with it.
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
        raise Error(404, "err.plugin_not_found", "Plugin not found")
    return p


def _validate_row(plugin: Plugin, table: str, row: dict) -> dict:
    schema = (plugin.table_schema or {}).get(table)
    if schema is None:
        raise Error(400, "err.table_not_plugin_schema",
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
        raise Error(400, "err.url_not_allowed",
                     "URL not allowed (SSRF protection / allowed_hosts)")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            r = await client.request(data.method, data.url, headers=data.headers, content=data.body)
        return {"status": r.status_code, "body": r.text[:5 * 1024 * 1024]}
    except Exception as exc:  # noqa: BLE001
        raise Error(502, "err.fetch_error", "Fetch error: {reason}", reason=exc)
