"""What an admin gets to see when something went wrong.

Traccoon writes its protocol in two places, and until now neither could be read from the
surface. The DB tables (agent runs, flow runs, job runs, inbound deliveries) were visible
only through the project views — bound to project membership, not to the admin right, and
runs without a ticket did not appear there at all. The application log went to stdout and
was reachable only over `docker logs` on the host.

This module joins the first kind into one list. The second kind stays where it is and is
fetched from the deployer, because that service already holds the docker socket. The
backend does not get one: it faces the web, and the socket is root on the host.

Entries are normalised so a single list can carry them: when, from where, how bad, one
line of title, one block of detail, and a reference the frontend can link to.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("adminlogs")

DEPLOYER_URL = os.getenv("DEPLOYER_URL", "http://deployer:8661")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")

Level = Literal["error", "warn", "info"]

#: The sources the list can be filtered by.
SOURCES = ("runs", "workflows", "jobs", "inbound")

#: Cap per source. The list is a look at what happened, not an export.
MAX_LIMIT = 500


def _entry(ts: dt.datetime | None, source: str, level: Level, title: str,
           detail: str = "", ref: dict | None = None) -> dict[str, Any]:
    return {
        "ts": ts.isoformat() if ts else None,
        "source": source,
        "level": level,
        "title": title[:300],
        # Long tracebacks are what one wants to read here, but not in the list: the
        # frontend shows the detail on demand.
        "detail": (detail or "")[:8000],
        "ref": ref or {},
    }


async def _runs(db: AsyncSession, since: dt.datetime, only_errors: bool, limit: int) -> list[dict]:
    from ..models.agents import Run

    bad = ("failed", "loop_exhausted", "blocked")
    stmt = select(Run).where(Run.started_at >= since).order_by(Run.started_at.desc()).limit(limit)
    if only_errors:
        stmt = stmt.where(Run.status.in_(bad))
    out = []
    for r in (await db.execute(stmt)).scalars().all():
        level: Level = "error" if r.status in ("failed", "loop_exhausted") else (
            "warn" if r.status == "blocked" else "info")
        out.append(_entry(
            r.finished_at or r.started_at, "runs", level,
            f"{r.agent} · {r.status} · {r.task_id or '—'}",
            r.error or r.summary or r.last_text or "",
            {"run_id": r.id, "issue_id": r.issue_id, "job_id": r.job_id, "project_id": r.project_id},
        ))
    return out


async def _workflows(db: AsyncSession, since: dt.datetime, only_errors: bool, limit: int) -> list[dict]:
    from ..models.workflow import WorkflowInstance

    stmt = (select(WorkflowInstance).where(WorkflowInstance.started_at >= since)
            .order_by(WorkflowInstance.started_at.desc()).limit(limit))
    out = []
    for i in (await db.execute(stmt)).scalars().all():
        # `status` is an enum here, not a string — its value is what the surface knows.
        status = getattr(i.status, "value", str(i.status))
        if only_errors and status not in ("failed", "cancelled"):
            continue
        level: Level = "error" if status == "failed" else ("warn" if status == "cancelled" else "info")
        out.append(_entry(
            i.finished_at or i.started_at, "workflows", level,
            f"Flow {i.definition_id} · {status}" + (f" · {i.source}" if i.source else ""),
            i.error or "",
            {"instance_id": i.id, "definition_id": i.definition_id, "issue_id": i.issue_id},
        ))
    return out


async def _jobs(db: AsyncSession, since: dt.datetime, only_errors: bool, limit: int) -> list[dict]:
    from ..models.ops import Job, JobRun

    stmt = (select(JobRun, Job.name).join(Job, Job.id == JobRun.job_id)
            .where(JobRun.started_at >= since).order_by(JobRun.started_at.desc()).limit(limit))
    if only_errors:
        stmt = stmt.where(JobRun.status == "error")
    out = []
    for jr, name in (await db.execute(stmt)).all():
        level: Level = "error" if jr.status == "error" else "info"
        out.append(_entry(
            jr.finished_at or jr.started_at, "jobs", level,
            f"{name} · {jr.status}",
            jr.error or jr.output or "",
            {"job_id": jr.job_id, "job_run_id": jr.id},
        ))
    return out


async def _inbound(db: AsyncSession, since: dt.datetime, only_errors: bool, limit: int) -> list[dict]:
    from ..models.ops import InboundDelivery

    stmt = (select(InboundDelivery).where(InboundDelivery.received_at >= since)
            .order_by(InboundDelivery.received_at.desc()).limit(limit))
    if only_errors:
        stmt = stmt.where(InboundDelivery.status.in_(("dropped", "error")))
    out = []
    for d in (await db.execute(stmt)).scalars().all():
        level: Level = "error" if d.status in ("dropped", "error") else "info"
        out.append(_entry(
            d.finished_at or d.received_at, "inbound", level,
            f"{d.channel} · {d.route or d.target or '—'} · {d.status}",
            # NOT the body: it is raw input from outside and can hold anything. The
            # outcome and the last error say what happened, and that is the question here.
            d.last_error or d.outcome or "",
            {"delivery_id": d.id, "attempts": d.attempts},
        ))
    return out


async def collect(db: AsyncSession, *, sources: tuple[str, ...] = SOURCES,
                  hours: int = 24, only_errors: bool = False, query: str = "",
                  limit: int = 200) -> list[dict]:
    """The merged list, newest first.

    Each source is asked for `limit` rows and the merge cuts back to `limit` — otherwise
    one chatty source would push a quiet one out of the window entirely.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    since = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=max(1, min(hours, 24 * 90)))
    fetch = {"runs": _runs, "workflows": _workflows, "jobs": _jobs, "inbound": _inbound}

    entries: list[dict] = []
    for name in sources:
        if name in fetch:
            entries += await fetch[name](db, since, only_errors, limit)

    if query:
        needle = query.lower()
        entries = [e for e in entries
                   if needle in e["title"].lower() or needle in e["detail"].lower()]

    entries.sort(key=lambda e: e["ts"] or "", reverse=True)
    return entries[:limit]


async def container_services() -> list[dict]:
    """The stack's containers, asked of the deployer."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{DEPLOYER_URL}/stack/services", json={},
                              headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
        r.raise_for_status()
        return r.json().get("services", [])


async def container_log(service: str, tail: int = 300) -> dict:
    """Container log of one service, asked of the deployer."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{DEPLOYER_URL}/stack/logs",
                              json={"service": service, "tail": tail},
                              headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
        r.raise_for_status()
        return r.json()
