"""Testumgebungen pro Ticket: Port-Allokation (Redis-SET) + Deployer-Preview-Server.

Isolation: compose.preview.yml im Worktree (keine Traefik-Labels/Bind-Mounts,
named volumes, PREVIEW_PORT-Mapping). Docker-Socket bleibt im Deployer.
"""
from __future__ import annotations

import json
import logging
import os

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import PREFIX, get_redis
from ..models.ticket import Issue
from ..worker import gitops

log = logging.getLogger("traccoon.testenv")
DEPLOYER_URL = os.getenv("DEPLOYER_URL", "http://deployer:8661")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
TESTENV_HOST = os.getenv("TESTENV_HOST", "localhost")
# Host-Pfad des Workspace (wie ihn der Deployer sieht) — kommt aus der Umgebung (.env).
WORKSPACE_HOST_PATH = os.getenv("WORKSPACE_HOST_PATH", "")
PORT_RANGE = os.getenv("TESTENV_PORT_RANGE", "8100-8199")
_LO, _HI = (int(x) for x in PORT_RANGE.split("-"))
_SET = f"{PREFIX}testenv:ports"


async def _alloc_port() -> int | None:
    r = get_redis()
    for p in range(_LO, _HI + 1):
        if await r.sadd(_SET, p):
            return p
    return None


async def _free_port(port: int) -> None:
    await get_redis().srem(_SET, port)


def _worktree_host(issue: Issue, project_key: str) -> str:
    # gitops.worktree_path liefert /workspace/... (Worker-Sicht) → auf Host-Sicht mappen
    rel = f".traccoon-worktrees/{project_key.lower()}/{issue.key}"
    return f"{WORKSPACE_HOST_PATH}/{rel}"


def _preview_env(project, issue: Issue) -> dict:
    """Env für die Preview: Projekt-Vorgabe, vom Ticket überschreibbar."""
    from ..core.security import decrypt_secret

    env: dict = {}
    for enc in (getattr(project, "testenv_env_enc", ""), issue.testenv_env_enc):
        if not enc:
            continue
        try:
            env.update(json.loads(decrypt_secret(enc)))
        except Exception:  # noqa: BLE001
            log.warning("testenv-Env konnte nicht gelesen werden (%s)", issue.key)
    return env


async def start_testenv(db: AsyncSession, issue: Issue, project_key: str) -> dict:
    port = await _alloc_port()
    if port is None:
        issue.testenv_status = "error"
        issue.testenv_error = "kein freier Port"
        await db.commit()
        return {"ok": False, "error": "kein freier Port"}
    name = f"traccoon-preview-{issue.key.lower()}"
    workdir = _worktree_host(issue, project_key)
    cfile = f"{workdir}/compose.preview.yml"
    issue.testenv_status = "starting"
    await db.commit()
    from ..models.project import Project
    project = await db.get(Project, issue.project_id)
    payload = {
        "project_name": name, "compose_file": cfile, "port": port, "workdir": workdir,
        "mode": getattr(project, "testenv_mode", "compose") or "compose",
        "container_port": getattr(project, "testenv_container_port", 8080),
        "prestart": getattr(project, "testenv_prestart", "") or "",
        "env": _preview_env(project, issue),
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{DEPLOYER_URL}/preview/up", json=payload,
                                  headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
        ok = r.status_code == 200 and r.json().get("ok")
    except Exception as exc:  # noqa: BLE001
        ok, r = False, None
        issue.testenv_error = str(exc)
    if ok:
        issue.testenv_status = "running"
        issue.testenv_port = port
        issue.testenv_container = name
        issue.testenv_url = f"http://{TESTENV_HOST}:{port}"
    else:
        issue.testenv_status = "error"
        await _free_port(port)
        if r is not None:
            issue.testenv_error = (r.json().get("log", "") or "")[-500:]
    await db.commit()
    return {"ok": ok, "url": issue.testenv_url}


async def cleanup_orphan_previews() -> dict:
    """Beim Start abgleichen: Preview-Stacks ohne laufendes Ticket abraeumen,
    Port-Reservierungen ohne Ticket freigeben."""
    from sqlalchemy import select

    from ..db import SessionLocal

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Issue).where(Issue.testenv_status == "running"))).scalars().all()
        keep = [i.testenv_container for i in rows if i.testenv_container]
        live_ports = {str(i.testenv_port) for i in rows if i.testenv_port}

    # Reservierte Ports, zu denen es kein laufendes Ticket mehr gibt, wieder freigeben.
    r = get_redis()
    reserved = await r.smembers(_SET)
    stale = [p for p in reserved if p not in live_ports]
    if stale:
        await r.srem(_SET, *stale)

    removed = []
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{DEPLOYER_URL}/preview/cleanup", json={"keep": keep},
                                     headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
        if resp.status_code == 200:
            removed = resp.json().get("removed", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("Preview-Aufräumen fehlgeschlagen: %s", exc)
    if removed or stale:
        log.info("Previews aufgeräumt: %d Stacks, %d Ports freigegeben", len(removed), len(stale))
    return {"removed": removed, "freed_ports": stale}


async def stop_testenv(db: AsyncSession, issue: Issue, project_key: str) -> None:
    name = issue.testenv_container or f"traccoon-preview-{issue.key.lower()}"
    cfile = f"{_worktree_host(issue, project_key)}/compose.preview.yml"
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            await client.post(f"{DEPLOYER_URL}/preview/down",
                              json={"project_name": name, "compose_file": cfile},
                              headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
    except Exception:  # noqa: BLE001
        pass
    if issue.testenv_port:
        await _free_port(issue.testenv_port)
    issue.testenv_status = ""
    issue.testenv_url = None
    issue.testenv_container = None
    issue.testenv_port = None
    await db.commit()
