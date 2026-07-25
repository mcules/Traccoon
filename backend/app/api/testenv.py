"""Testumgebungen je Projekt (ABC-18): Branch-Umgebungen, Übersicht, Logs.

Die Ticket-Umgebung wird weiterhin am Ticket gesteuert (`lifecycle.py`); hier liegt
alles, was am Projekt hängt: freie Branches, kombinierte Übersicht und Log-Abruf.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import ProjectRole
from ..models.testenv import BranchTestenv
from ..models.ticket import Issue
from ..services import testenv as svc
from .deps import Access, get_project_access, require_role

router = APIRouter(tags=["testenv"])

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspace")


class BranchTestenvOut(BaseModel):
    id: int
    project_id: int
    branch: str
    status: str
    url: str | None
    container: str | None
    port: int | None
    error: str | None
    model_config = {"from_attributes": True}


class BranchIn(BaseModel):
    branch: str = Field(min_length=1, max_length=255)


class LogsIn(BaseModel):
    service: str | None = None
    tail: int = Field(default=200, ge=1, le=2000)


def _require_enabled(access: Access) -> None:
    if not access.project.testenv_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Testumgebungen sind für dieses Projekt aus")


@router.get("/projects/{project_id}/branches")
async def list_branches(access: Access = Depends(get_project_access)):
    """Branch-Namen des Projekt-Repos (lokal + Remote-Tracking, dedupliziert)."""
    workdir = os.path.join(WORKSPACE_ROOT, access.project.key.lower())
    if not os.path.isdir(os.path.join(workdir, ".git")):
        return []
    p = await asyncio.create_subprocess_exec(
        "git", "-C", workdir, "for-each-ref", "--format=%(refname:short)",
        "refs/heads", "refs/remotes/origin",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await p.communicate()
    names: list[str] = []
    for raw in out.decode("utf-8", "replace").splitlines():
        name = raw.strip()
        if not name or name.endswith("/HEAD"):
            continue
        name = name[len("origin/"):] if name.startswith("origin/") else name
        if name and name not in names:
            names.append(name)
    return sorted(names)


@router.get("/projects/{project_id}/branch-testenvs", response_model=list[BranchTestenvOut])
async def list_branch_testenvs(access: Access = Depends(get_project_access),
                               db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(
        select(BranchTestenv).where(BranchTestenv.project_id == access.project.id)
        .order_by(BranchTestenv.id.desc()))).scalars().all()
    return list(rows)


@router.post("/projects/{project_id}/branch-testenvs")
async def start_branch_testenv(
    data: BranchIn,
    access: Access = Depends(require_role(ProjectRole.member)),
    db: AsyncSession = Depends(get_session),
):
    _require_enabled(access)
    return await svc.start_branch_testenv(db, access.project, data.branch.strip(), access.user.id)


@router.post("/projects/{project_id}/branch-testenvs/{env_id}/stop", status_code=204)
async def stop_branch_testenv(
    env_id: int,
    access: Access = Depends(require_role(ProjectRole.member)),
    db: AsyncSession = Depends(get_session),
):
    row = await db.get(BranchTestenv, env_id)
    if row is None or row.project_id != access.project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Testumgebung nicht gefunden")
    await svc.stop_branch_testenv(db, access.project, row)


@router.get("/projects/{project_id}/testenvs")
async def list_testenvs(access: Access = Depends(get_project_access),
                        db: AsyncSession = Depends(get_session)):
    """Kombinierte Übersicht: DB-Zustand (Ticket + Branch), angereichert um das,
    was der Runner gerade wirklich laufen sieht."""
    issues = (await db.execute(
        select(Issue).where(Issue.project_id == access.project.id,
                            Issue.testenv_status.notin_(["", "stopped"]))
    )).scalars().all()
    branches = (await db.execute(
        select(BranchTestenv).where(BranchTestenv.project_id == access.project.id)
    )).scalars().all()

    live = {s.get("project_name"): s for s in await svc.runner_list()}
    out = []
    for i in issues:
        name = i.testenv_container or svc.ticket_container(i)
        out.append({
            "kind": "ticket", "ref": i.key, "label": f"{i.key} · {i.summary}",
            "container": name, "status": i.testenv_status, "url": i.testenv_url,
            "port": i.testenv_port, "error": i.testenv_error,
            "services": (live.get(name) or {}).get("services", []),
        })
    for b in branches:
        name = b.container or svc.branch_container(b.id)
        out.append({
            "kind": "branch", "ref": b.id, "label": f"Branch {b.branch}",
            "container": name, "status": b.status, "url": b.url,
            "port": b.port, "error": b.error,
            "services": (live.get(name) or {}).get("services", []),
        })
    return out


@router.post("/projects/{project_id}/testenvs/{name}/logs")
async def testenv_logs(
    name: str, data: LogsIn,
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
):
    """Logs einer Umgebung. Der Name muss zu einer Umgebung DIESES Projekts gehören —
    sonst könnte man über den Runner fremde Stacks auslesen."""
    known = {svc.ticket_container(i) for i in (await db.execute(
        select(Issue).where(Issue.project_id == access.project.id))).scalars().all()}
    known |= {i.testenv_container for i in (await db.execute(
        select(Issue).where(Issue.project_id == access.project.id,
                            Issue.testenv_container.isnot(None)))).scalars().all()}
    known |= {svc.branch_container(b.id) for b in (await db.execute(
        select(BranchTestenv).where(BranchTestenv.project_id == access.project.id)
    )).scalars().all()}
    if name not in known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Testumgebung nicht gefunden")
    return await svc.runner_logs(name, data.service, data.tail)
