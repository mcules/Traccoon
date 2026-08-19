from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException

from ..core.fehler import Fehler
from ..db import get_session
from ..models.agents import Run, RunStep
from ..models.project import Project
from ..models.ticket import Issue
from ..models.user import User
from .deps import Access, build_access, get_current_user, get_project_access

router = APIRouter(tags=["runs"])


def _run_out(r: Run, issue_key: str) -> dict:
    return {"id": r.id, "issue_key": issue_key, "agent": r.agent, "phase": r.phase,
            "status": r.status, "provider": r.provider, "model": r.model,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "cost_usd": r.cost_usd, "iterations": r.iterations,
            "started_at": r.started_at, "finished_at": r.finished_at, "archived": r.archived,
            "summary": (r.summary or r.last_text or "")[:200]}


@router.get("/projects/{project_id}/runs")
async def project_runs(access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session),
                       limit: int = 40, archived: bool = False):
    """Flat list (the old behaviour). `archived=true` shows the archived runs."""
    rows = (
        await db.execute(
            select(Run, Issue.key).join(Issue, Issue.id == Run.issue_id)
            .where(Issue.project_id == access.project.id, Run.archived.is_(archived))
            .order_by(Run.id.desc()).limit(limit)
        )
    ).all()
    return [_run_out(r, key) for r, key in rows]


@router.get("/projects/{project_id}/runs/grouped")
async def project_runs_grouped(
    access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session),
    limit: int = 200, archived: bool = False,
):
    """Agent runs grouped by ticket (TRA-29): one group per ticket, the most recent first.

    `limit` limits the runs considered (not the groups); when it is reached, `truncated`
    reports that older runs were left out.
    """
    rows = (
        await db.execute(
            select(Run, Issue.key, Issue.summary, Issue.archived)
            .join(Issue, Issue.id == Run.issue_id)
            .where(Issue.project_id == access.project.id, Run.archived.is_(archived))
            .order_by(Run.id.desc()).limit(limit)
        )
    ).all()
    groups: dict[str, dict] = {}
    for r, key, summary, issue_archived in rows:
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "issue_key": key, "issue_summary": summary, "issue_archived": issue_archived,
                "runs": [], "cost_usd": 0.0, "output_tokens": 0,
            }
        g["runs"].append(_run_out(r, key))
        g["cost_usd"] += r.cost_usd or 0.0
        g["output_tokens"] += r.output_tokens or 0
    return {"groups": list(groups.values()), "truncated": len(rows) >= limit}


@router.get("/projects/{project_id}/active-runs")
async def active_runs(access: Access = Depends(get_project_access)):
    """What the worker is really working on right now (the Redis hash, not the database).

    Shows runs whose ticket status has not been written yet as well.
    """
    import json
    import time

    from ..core.redis import PREFIX, get_redis

    raw = await get_redis().hgetall(f"{PREFIX}active_processes")
    out = []
    now = time.time()
    for entry in raw.values():
        try:
            d = json.loads(entry)
        except json.JSONDecodeError:
            continue
        if d.get("project_id") != access.project.id:
            continue
        d["running_seconds"] = int(now - (d.get("started_at") or now))
        out.append(d)
    return sorted(out, key=lambda d: d["running_seconds"], reverse=True)


@router.get("/runs/{run_id}/steps")
async def run_steps(run_id: int, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    # Access only for project members of the ticket the run belongs to (404 instead of a leak).
    run = await db.get(Run, run_id)
    if run is None:
        raise Fehler(404, "err.run_not_found", "Run not found")
    issue = await db.get(Issue, run.issue_id) if run.issue_id else None
    project = await db.get(Project, issue.project_id) if issue else None
    if project is None:
        # Job run without a ticket: admins only (the job owner binding is missing on the run).
        if user.global_role.value != "admin":
            raise Fehler(404, "err.run_not_found", "Run not found")
    else:
        access = await build_access(project, user, db)
        from ..models.enums import ProjectRole
        if not access.has_role(ProjectRole.viewer):
            raise Fehler(404, "err.run_not_found", "Run not found")
    rows = (await db.execute(select(RunStep).where(RunStep.run_id == run_id)
                             .order_by(RunStep.seq))).scalars().all()
    return [{"seq": s.seq, "role": s.role, "tool": s.tool_name, "content": s.content[:1500]} for s in rows]
