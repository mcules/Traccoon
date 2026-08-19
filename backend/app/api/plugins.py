"""Plugin system (compact): zip in the database, serving, generic table CRUD (JSON, schema whitelist),
SSRF protected fetch proxy."""
from __future__ import annotations

import io
import ipaddress
import json
import socket
import zipfile
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
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
                        "entry": p.entry, "contributions": p.contributions})
    return out


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
    await db.flush()
    # alte Dateien ersetzen
    for f in (await db.execute(select(PluginFile).where(PluginFile.plugin_id == plugin.id))).scalars().all():
        await db.delete(f)
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


def _ctype(path: str) -> str:
    if path.endswith(".html"):
        return "text/html"
    if path.endswith(".js"):
        return "application/javascript"
    if path.endswith(".css"):
        return "text/css"
    if path.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


@router.get("/{slug}/app/{path:path}")
async def serve_file(slug: str, path: str, db: AsyncSession = Depends(get_session)):
    plugin = (await db.execute(select(Plugin).where(Plugin.slug == slug))).scalar_one_or_none()
    if plugin is None:
        raise Fehler(404, "err.plugin_not_found", "Plugin not found")
    f = (await db.execute(select(PluginFile).where(PluginFile.plugin_id == plugin.id,
                                                   PluginFile.path == (path or plugin.entry)))).scalar_one_or_none()
    if f is None:
        raise Fehler(404, "err.file_not_found", "File not found")
    return Response(content=f.data, media_type=f.content_type, headers={"Cache-Control": "no-cache"})


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
        raise Fehler(502, "err.fetch_error", "Fetch error: {grund}", grund=exc)
