from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException

from ..db import get_session
from ..models.agents import Run, RunStep
from ..models.project import Project
from ..models.ticket import Issue
from ..models.user import User
from .deps import Access, build_access, get_current_user, get_project_access

router = APIRouter(tags=["runs"])


@router.get("/projects/{project_id}/runs")
async def project_runs(access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session),
                       limit: int = 40):
    rows = (
        await db.execute(
            select(Run, Issue.key).join(Issue, Issue.id == Run.issue_id)
            .where(Issue.project_id == access.project.id)
            .order_by(Run.id.desc()).limit(limit)
        )
    ).all()
    return [{"id": r.id, "issue_key": key, "agent": r.agent, "phase": r.phase, "status": r.status,
             "provider": r.provider, "model": r.model, "input_tokens": r.input_tokens,
             "output_tokens": r.output_tokens, "cost_usd": r.cost_usd, "iterations": r.iterations,
             "started_at": r.started_at, "finished_at": r.finished_at,
             "summary": (r.summary or r.last_text or "")[:200]} for r, key in rows]


@router.get("/projects/{project_id}/active-runs")
async def active_runs(access: Access = Depends(get_project_access)):
    """Was der Worker gerade wirklich bearbeitet (Redis-Hash, nicht die DB).

    Zeigt auch Läufe, deren Ticket-Status noch nicht geschrieben wurde.
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
    # Zugriff nur für Projektmitglieder des zum Lauf gehörenden Tickets (sonst 404 statt Leak).
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Lauf nicht gefunden")
    issue = await db.get(Issue, run.issue_id) if run.issue_id else None
    project = await db.get(Project, issue.project_id) if issue else None
    if project is None:
        # Job-Lauf ohne Ticket: nur Admin (Job-Owner-Bindung fehlt am Run).
        if user.global_role.value != "admin":
            raise HTTPException(404, "Lauf nicht gefunden")
    else:
        access = await build_access(project, user, db)
        from ..models.enums import ProjectRole
        if not access.has_role(ProjectRole.viewer):
            raise HTTPException(404, "Lauf nicht gefunden")
    rows = (await db.execute(select(RunStep).where(RunStep.run_id == run_id)
                             .order_by(RunStep.seq))).scalars().all()
    return [{"seq": s.seq, "role": s.role, "tool": s.tool_name, "content": s.content[:1500]} for s in rows]
