"""The configuration audit: read the findings, put one aside, look at the history.

Administration, deliberately. The findings name paths and rules of the agent configurations
on the host — that is caretaking of the machine, not something every person in the house has
a use for. The collector needs no route here at all: the backend asks it, not the other way
round.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.agentshield import SEVERITIES, ShieldFinding, ShieldRun, ShieldRunConfig
from ..models.user import User
from ..services import agentshield as service
from .deps import require_admin

router = APIRouter(prefix="/agentshield", tags=["agentshield"])

# How many runs the history hands out at most. The page draws a curve out of it; beyond a
# couple of months of daily runs a column is thinner than a hair.
HISTORY_LIMIT = 120


class StatusIn(BaseModel):
    status: str


class ScanIn(BaseModel):
    trigger: str = "hand"


def _finding(row: ShieldFinding) -> dict:
    return {
        "id": row.id, "key": row.key, "config": row.config, "severity": row.severity,
        "title": row.title, "file": row.file, "rule": row.rule, "detail": row.detail,
        "status": row.status,
        "first_seen": row.first_seen, "last_seen": row.last_seen, "seen_count": row.seen_count,
    }


@router.get("/findings")
async def list_findings(status: str = Query("open"), config: str = Query(""),
                        _: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_session)):
    """The findings, by state. `status=all` for everything, including what is gone."""
    query = select(ShieldFinding)
    if status != "all":
        query = query.where(ShieldFinding.status == status)
    if config:
        query = query.where(ShieldFinding.config == config)
    rows = (await db.execute(query)).scalars().all()
    # Sorted the way the page reads: worst first, then by configuration and title. In SQL
    # this would be a CASE over five strings; the list is a few hundred rows at most.
    order = {name: i for i, name in enumerate(SEVERITIES)}
    rows.sort(key=lambda r: (order.get(r.severity, 9), r.config or "", r.title or ""))
    return [_finding(r) for r in rows]


@router.get("/overview")
async def overview(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """What the head of the page and the tile on the start page need.

    Open findings per severity, how many were put aside, which configurations are affected,
    and when the last run was. One call: the tile has room for three numbers and no reason to
    fetch three lists to get them.
    """
    per_state = dict((await db.execute(
        select(ShieldFinding.status, func.count(ShieldFinding.id))
        .group_by(ShieldFinding.status))).all())
    per_severity = dict((await db.execute(
        select(ShieldFinding.severity, func.count(ShieldFinding.id))
        .where(ShieldFinding.status == "open").group_by(ShieldFinding.severity))).all())
    stacks = (await db.execute(
        select(func.count(func.distinct(ShieldFinding.config)))
        .where(ShieldFinding.status == "open"))).scalar_one()
    # Behind every figure: which configuration contributes how much. A head figure says how
    # much, never where — "five critical" over thirteen stacks is a question, not an answer,
    # and the answer belongs in the note that opens over the number.
    per_config: dict[str, dict[str, int]] = {}
    for config, severity, count in (await db.execute(
            select(ShieldFinding.config, ShieldFinding.severity, func.count(ShieldFinding.id))
            .where(ShieldFinding.status == "open")
            .group_by(ShieldFinding.config, ShieldFinding.severity))).all():
        per_config.setdefault(config, {name: 0 for name in SEVERITIES})[severity] = count
    last = (await db.execute(
        select(ShieldRun).order_by(ShieldRun.started_at.desc()).limit(1))).scalar_one_or_none()
    return {
        "open": {name: per_severity.get(name, 0) for name in SEVERITIES},
        "ignored": per_state.get("ignored", 0),
        "fixed": per_state.get("fixed", 0),
        "stacks": stacks,
        "by_config": [{"config": name, **counts} for name, counts in sorted(per_config.items())],
        "last_run": None if last is None else {
            "id": last.id, "started_at": last.started_at, "finished_at": last.finished_at,
            "trigger": last.trigger, "configs": last.configs, "findings": last.findings,
            "new_count": last.new_count, "fixed_count": last.fixed_count,
        },
    }


@router.get("/history")
async def history(limit: int = Query(HISTORY_LIMIT, ge=2, le=HISTORY_LIMIT),
                  _: User = Depends(require_admin),
                  db: AsyncSession = Depends(get_session)):
    """The runs, oldest first, each with what it found per configuration.

    Oldest first because that is the direction a curve is drawn in, and the caller should not
    have to turn the list round to draw it.
    """
    runs = (await db.execute(
        select(ShieldRun).order_by(ShieldRun.started_at.desc()).limit(limit))).scalars().all()
    runs.reverse()
    ids = [r.id for r in runs]
    per_run: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        for row in (await db.execute(
                select(ShieldRunConfig).where(ShieldRunConfig.run_id.in_(ids))
                .order_by(ShieldRunConfig.config))).scalars().all():
            per_run[row.run_id].append({
                "config": row.config, "grade": row.grade, "error": row.error,
                **{name: getattr(row, name) for name in SEVERITIES},
            })
    return [{
        "id": r.id, "started_at": r.started_at, "finished_at": r.finished_at,
        "trigger": r.trigger, "configs": r.configs, "findings": r.findings,
        "new_count": r.new_count, "fixed_count": r.fixed_count,
        **{name: getattr(r, name) for name in SEVERITIES},
        "per_config": per_run.get(r.id, []),
    } for r in runs]


@router.post("/findings/{fid}/status")
async def set_status(fid: int, data: StatusIn, _: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_session)):
    """Put a finding aside, or watch it again.

    Only these two: `fixed` is not a decision but an observation of the scanner, and a person
    setting it by hand would be telling the audit something it is about to correct.
    """
    if data.status not in ("open", "ignored"):
        raise Error(400, "err.invalid_status", "Invalid status")
    row = await db.get(ShieldFinding, fid)
    if row is None:
        raise Error(404, "err.finding_not_found", "Finding not found")
    row.status = data.status
    await db.commit()
    await db.refresh(row)
    return _finding(row)


@router.post("/scan")
async def scan_now(data: ScanIn | None = None, _: User = Depends(require_admin),
                   db: AsyncSession = Depends(get_session)):
    """Run the audit now. Answers with the summary of the run it just wrote."""
    summary = await service.scan(db, trigger=(data.trigger if data else "hand"))
    await db.commit()
    return summary
