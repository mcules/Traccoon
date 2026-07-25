"""Testumgebungen: Port-Allokation (Redis-SET) + Deployer als Runner.

Zwei Ausprägungen (ABC-18), identische Bau-Mechanik:
  * Ticket-Testumgebung — Zustand in Feldern am `Issue`, Container `test-<ticket-id>`
  * Branch-Testumgebung — Zustand in `branch_testenvs`, Container `test-b<env-id>`

Isolation: eigenes Compose-Projekt je Umgebung (eigenes Netz + eigene leere Volumes),
nur der Einstiegs-Service bekommt einen Host-Port, keine Proxy-Labels, keine
Host-Bind-Mounts. Der Docker-Socket bleibt beim Deployer.
"""
from __future__ import annotations

import json
import logging
import os

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import PREFIX, get_redis
from ..models.ticket import Issue

log = logging.getLogger("traccoon.testenv")
DEPLOYER_URL = os.getenv("DEPLOYER_URL", "http://deployer:8661")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
# Host-Pfad des Workspace (wie ihn der Deployer sieht) — kommt aus der Umgebung (.env).
WORKSPACE_HOST_PATH = os.getenv("WORKSPACE_HOST_PATH", "")
_SET = f"{PREFIX}testenv:ports"

# Defaults; zur Laufzeit über AppSettings überschreibbar (Admin → Testumgebungen).
DEFAULTS = {
    "testenv_host": os.getenv("TESTENV_HOST", "localhost"),
    "testenv_port_lo": "21000",
    "testenv_port_hi": "21999",
    "testenv_max_concurrent": "5",
    "testenv_max_builds": os.getenv("TESTENV_MAX_BUILDS", "2"),
    "testenv_mem_limit": "2g",
    "testenv_cpus": "2",
}
SETTING_KEYS = tuple(DEFAULTS)


async def get_config(db: AsyncSession) -> dict[str, str]:
    """Globale Testumgebungs-Konfiguration (DB-persistent, ohne Neustart wirksam)."""
    from .appsettings import get_setting
    return {k: (await get_setting(db, k, "") or DEFAULTS[k]) for k in SETTING_KEYS}


def _int(cfg: dict, key: str) -> int:
    raw = str(cfg.get(key, DEFAULTS[key]))
    return int(raw) if raw.lstrip("-").isdigit() else int(DEFAULTS[key])


async def _alloc_port(cfg: dict) -> int | None:
    """Atomar über ein Redis-SET. Zusätzlich gedeckelt auf `testenv_max_concurrent`,
    damit viele parallele Umgebungen den Host nicht erschöpfen."""
    r = get_redis()
    if await r.scard(_SET) >= _int(cfg, "testenv_max_concurrent"):
        return None
    for p in range(_int(cfg, "testenv_port_lo"), _int(cfg, "testenv_port_hi") + 1):
        if await r.sadd(_SET, p):
            return p
    return None


async def _free_port(port: int) -> None:
    await get_redis().srem(_SET, port)


def _worktree_host(project_key: str, name: str) -> str:
    """Host-Sicht auf den Worktree (gitops.worktree_path liefert die Worker-Sicht)."""
    return f"{WORKSPACE_HOST_PATH}/.traccoon-worktrees/{project_key.lower()}/{name}"


def _decrypt_env(*encs: str) -> dict:
    from ..core.security import decrypt_secret
    env: dict = {}
    for enc in encs:
        if not enc:
            continue
        try:
            env.update(json.loads(decrypt_secret(enc)))
        except Exception:  # noqa: BLE001
            log.warning("testenv-Env konnte nicht gelesen werden")
    return env


def _demo_login(project) -> tuple[str, str]:
    try:
        d = json.loads(getattr(project, "testenv_demo_login", "") or "{}")
    except (ValueError, TypeError):
        d = {}
    return d.get("email") or "demo@demo.com", d.get("password") or "demo1234"


def _runtime_env(project, cfg: dict, port: int, extra_enc: str = "") -> dict:
    """Projekt-Env plus injizierte Laufzeitwerte. Injizierte stehen ZULETZT und gewinnen —
    sonst könnte eine Projekt-Env den Port oder die Wegwerf-Secrets überschreiben."""
    email, password = _demo_login(project)
    env = _decrypt_env(getattr(project, "testenv_env_enc", ""), extra_enc)
    env.update({
        "TESTENV_PORT": str(port),
        "TESTENV_MEM_LIMIT": str(cfg.get("testenv_mem_limit", DEFAULTS["testenv_mem_limit"])),
        "TESTENV_CPUS": str(cfg.get("testenv_cpus", DEFAULTS["testenv_cpus"])),
        "SEED_DEMO_EMAIL": email,
        "SEED_DEMO_PASSWORD": password,
        # Keine echten Fremdsysteme aus einer Wegwerf-Umgebung heraus ansprechen.
        "MOCK_MODE": "true",
        "PREVIEW_PORT": str(port),   # Altname, solange Bestands-Composes ihn nutzen
    })
    return env


def _url(project, cfg: dict, port: int) -> str:
    tmpl = getattr(project, "testenv_url_template", "") or "http://{host}:{port}"
    host = cfg.get("testenv_host") or DEFAULTS["testenv_host"]
    try:
        return tmpl.format(host=host, port=port)
    except (KeyError, IndexError):
        return f"http://{host}:{port}"


def _compose_file(project, workdir: str) -> str:
    name = getattr(project, "testenv_compose_file", "") or "compose.preview.yml"
    return f"{workdir}/{name}"


async def _up(payload: dict) -> tuple[bool, str]:
    """Start beim Runner anstoßen. Liefert (ok, Log/Fehlertext)."""
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{DEPLOYER_URL}/preview/up", json=payload,
                                  headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    try:
        data = r.json()
    except ValueError:
        return False, r.text[-2000:]
    return bool(r.status_code == 200 and data.get("ok")), (data.get("log") or "")[-4000:]


async def _down(project_name: str, compose_file: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            await client.post(f"{DEPLOYER_URL}/preview/down",
                              json={"project_name": project_name, "compose_file": compose_file},
                              headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
    except Exception as exc:  # noqa: BLE001
        log.warning("Testumgebung %s stoppen fehlgeschlagen: %s", project_name, exc)


def _base_payload(project, cfg: dict, name: str, workdir: str, port: int,
                  extra_enc: str = "") -> dict:
    return {
        "project_name": name,
        "compose_file": _compose_file(project, workdir),
        "dockerfile": getattr(project, "testenv_dockerfile", "") or "Dockerfile",
        "port": port,
        "workdir": workdir,
        "mode": getattr(project, "testenv_mode", "compose") or "compose",
        "container_port": getattr(project, "testenv_container_port", 8080),
        "prestart": getattr(project, "testenv_prestart", "") or "",
        "env": _runtime_env(project, cfg, port, extra_enc),
        "mem_limit": cfg.get("testenv_mem_limit", DEFAULTS["testenv_mem_limit"]),
        "cpus": cfg.get("testenv_cpus", DEFAULTS["testenv_cpus"]),
        "max_builds": _int(cfg, "testenv_max_builds"),
    }


# ── Ticket-Testumgebung ──────────────────────────────────────────────────────

def ticket_container(issue: Issue) -> str:
    return f"test-{issue.key.lower()}"


async def start_testenv(db: AsyncSession, issue: Issue, project_key: str) -> dict:
    from ..models.project import Project
    project = await db.get(Project, issue.project_id)
    cfg = await get_config(db)
    port = await _alloc_port(cfg)
    if port is None:
        issue.testenv_status = "error"
        issue.testenv_error = ("Kein freier Port bzw. Höchstzahl paralleler Testumgebungen "
                               f"({cfg['testenv_max_concurrent']}) erreicht.")
        await db.commit()
        return {"ok": False, "error": issue.testenv_error}

    name = ticket_container(issue)
    workdir = _worktree_host(project_key, issue.key)
    issue.testenv_status = "starting"
    issue.testenv_error = None
    await db.commit()

    payload = _base_payload(project, cfg, name, workdir, port, issue.testenv_env_enc)
    ok, logtext = await _up(payload)
    if ok:
        issue.testenv_status = "running"
        issue.testenv_port = port
        issue.testenv_container = name
        issue.testenv_url = _url(project, cfg, port)
        issue.testenv_error = None
    else:
        issue.testenv_status = "error"
        issue.testenv_error = logtext[-4000:]
        await _free_port(port)
    await db.commit()
    return {"ok": ok, "url": issue.testenv_url, "error": issue.testenv_error}


async def stop_testenv(db: AsyncSession, issue: Issue, project_key: str) -> None:
    from ..models.project import Project
    project = await db.get(Project, issue.project_id)
    name = issue.testenv_container or ticket_container(issue)
    await _down(name, _compose_file(project, _worktree_host(project_key, issue.key)))
    if issue.testenv_port:
        await _free_port(issue.testenv_port)
    issue.testenv_status = ""
    issue.testenv_url = None
    issue.testenv_container = None
    issue.testenv_port = None
    issue.testenv_error = None
    await db.commit()


# ── Branch-Testumgebung ─────────────────────────────────────────────────────

def branch_container(env_id: int) -> str:
    return f"test-b{env_id}"


async def start_branch_testenv(db: AsyncSession, project, branch: str, user_id: int | None) -> dict:
    """Testumgebung für einen beliebigen Branch. Gleiche Bau-Mechanik wie am Ticket."""
    import datetime as dt

    from ..models.testenv import BranchTestenv

    cfg = await get_config(db)
    row = BranchTestenv(project_id=project.id, branch=branch, status="starting",
                        created_by=user_id, started_at=dt.datetime.now(tz=dt.timezone.utc))
    db.add(row)
    await db.commit()
    await db.refresh(row)

    port = await _alloc_port(cfg)
    if port is None:
        row.status = "error"
        row.error = ("Kein freier Port bzw. Höchstzahl paralleler Testumgebungen "
                     f"({cfg['testenv_max_concurrent']}) erreicht.")
        await db.commit()
        return {"ok": False, "id": row.id, "error": row.error}

    name = branch_container(row.id)
    workdir = _worktree_host(project.key, f"branch-{row.id}")
    payload = _base_payload(project, cfg, name, workdir, port)
    payload["branch"] = branch          # Runner frischt den Worktree auf diesen Branch auf
    payload["repo_dir"] = f"{WORKSPACE_HOST_PATH}/{project.key.lower()}"

    ok, logtext = await _up(payload)
    if ok:
        row.status, row.port, row.container = "running", port, name
        row.url = _url(project, cfg, port)
        row.error = None
    else:
        row.status, row.error = "error", logtext[-4000:]
        await _free_port(port)
    await db.commit()
    return {"ok": ok, "id": row.id, "url": row.url, "error": row.error}


async def stop_branch_testenv(db: AsyncSession, project, row) -> None:
    """Stoppt die Umgebung und LÖSCHT die Zeile — die Tabelle hält nur aktive/fehlerhafte."""
    workdir = _worktree_host(project.key, f"branch-{row.id}")
    await _down(row.container or branch_container(row.id), _compose_file(project, workdir))
    if row.port:
        await _free_port(row.port)
    await db.delete(row)
    await db.commit()


# ── Übersicht / Logs / Aufräumen ────────────────────────────────────────────

async def runner_list() -> list[dict]:
    """Was der Runner tatsächlich laufen sieht (docker ps, gefiltert auf test-*)."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{DEPLOYER_URL}/preview/list", json={},
                                  headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
        if r.status_code == 200:
            return r.json().get("stacks", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("Testumgebungs-Übersicht fehlgeschlagen: %s", exc)
    return []


async def runner_logs(project_name: str, service: str | None, tail: int) -> dict:
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{DEPLOYER_URL}/preview/logs",
                json={"project_name": project_name, "service": service, "tail": min(tail, 2000)},
                headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
        if r.status_code == 200:
            return r.json()
        return {"error": r.text[-1000:]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


async def cleanup_orphan_previews() -> dict:
    """Beim Start abgleichen: Testumgebungen ohne Zustand in der DB abräumen,
    Port-Reservierungen ohne Umgebung freigeben."""
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models.testenv import BranchTestenv

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Issue).where(Issue.testenv_status == "running"))).scalars().all()
        branches = (await db.execute(
            select(BranchTestenv).where(BranchTestenv.status == "running"))).scalars().all()
        keep = [i.testenv_container for i in rows if i.testenv_container]
        keep += [b.container for b in branches if b.container]
        live_ports = {str(i.testenv_port) for i in rows if i.testenv_port}
        live_ports |= {str(b.port) for b in branches if b.port}

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
        log.warning("Testumgebungen aufräumen fehlgeschlagen: %s", exc)
    if removed or stale:
        log.info("Testumgebungen aufgeräumt: %d Stacks, %d Ports freigegeben",
                 len(removed), len(stale))
    return {"removed": removed, "freed_ports": stale}
