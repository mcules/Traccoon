"""Projekt-Dashboard: berechnete Kennzahlen aus Tickets, Läufen und Kosten.

Alles wird zur Abfragezeit aggregiert — keine gespeicherten Gadgets, die
ohne Daten leer blieben.
"""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.agents import Run
from ..models.enums import StatusCategory, TicketAgentStatus
from ..models.ticket import Issue, WorkflowStatus
from .deps import Access, get_project_access

router = APIRouter(tags=["dashboard"])


@router.get("/projects/{project_id}/dashboard")
async def dashboard(access: Access = Depends(get_project_access),
                    db: AsyncSession = Depends(get_session), days: int = 14):
    pid = access.project.id
    since = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days)

    # Tickets nach Workflow-Kategorie (todo/in_progress/done)
    cat_rows = (await db.execute(
        select(WorkflowStatus.category, func.count(Issue.id))
        .join(Issue, Issue.status_id == WorkflowStatus.id)
        .where(Issue.project_id == pid).group_by(WorkflowStatus.category))).all()
    by_category = {c.value if hasattr(c, "value") else str(c): n for c, n in cat_rows}
    total = sum(by_category.values())

    # Tickets, die auf einen Menschen warten — die eigentliche Arbeitsliste
    waiting_states = [TicketAgentStatus.plan_review, TicketAgentStatus.to_test, TicketAgentStatus.hold]
    waiting = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.project_id == pid, Issue.agent_status.in_(waiting_states)))).scalar() or 0
    working = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.project_id == pid, Issue.agent_working.is_(True)))).scalar() or 0
    failed = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.project_id == pid, Issue.agent_status == TicketAgentStatus.failed))).scalar() or 0

    # Durchsatz: im Zeitfenster abgeschlossene Tickets
    done_recent = (await db.execute(
        select(func.count(Issue.id)).where(
            Issue.project_id == pid, Issue.resolved_at.isnot(None),
            Issue.resolved_at >= since))).scalar() or 0

    # Läufe + Kosten im Zeitfenster (Runs hängen über die Tickets am Projekt)
    run_rows = (await db.execute(
        select(Run.status, func.count(Run.id), func.coalesce(func.sum(Run.cost_usd), 0.0),
               func.coalesce(func.sum(Run.output_tokens), 0))
        .join(Issue, Issue.id == Run.issue_id)
        .where(Issue.project_id == pid, Run.started_at >= since)
        .group_by(Run.status))).all()
    runs_by_status = {s: {"count": n, "cost": float(c), "tokens": int(t)} for s, n, c, t in run_rows}
    runs_total = sum(v["count"] for v in runs_by_status.values())
    cost_total = round(sum(v["cost"] for v in runs_by_status.values()), 4)
    succ = runs_by_status.get("success", {}).get("count", 0)
    fail = runs_by_status.get("failed", {}).get("count", 0)
    success_rate = round(100 * succ / (succ + fail), 1) if (succ + fail) else None

    # Auslastung je Agentenrolle (offene, zugewiesene Tickets)
    agent_rows = (await db.execute(
        select(Issue.assigned_agent, func.count(Issue.id)).where(
            Issue.project_id == pid, Issue.assigned_agent.isnot(None),
            Issue.resolved_at.is_(None)).group_by(Issue.assigned_agent))).all()
    by_agent = [{"agent": a, "count": n} for a, n in agent_rows]

    return {
        "window_days": days,
        "tickets": {"total": total, "by_category": by_category,
                    "waiting_for_human": waiting, "working": working, "failed_state": failed},
        "throughput": {"done_in_window": done_recent},
        "runs": {"total": runs_total, "by_status": runs_by_status,
                 "cost_usd": cost_total, "success_rate": success_rate},
        "agents": sorted(by_agent, key=lambda d: d["count"], reverse=True),
    }
