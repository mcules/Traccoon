"""Native Steuer-Tools: der Assistent bedient Traccoon IM NAMEN seines Menschen und
STRIKT in dessen Rechten (build_access je Projekt). Generisch — keine personenbezogenen
Daten im Code. Rückgabe je Tool = knapper Text für den Agenten.
"""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import build_access
from ..models.agents import CostEntry, Run
from ..models.enums import TicketAgentStatus
from ..models.project import Project, ProjectMember
from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from ..models.user import User

_now = lambda: dt.datetime.now(tz=dt.timezone.utc)  # noqa: E731
_v = lambda x: getattr(x, "value", x) if x is not None else "—"  # Enum → Wert  # noqa: E731


def _def(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


# Deny-default: der Assistent bekommt diese nur mit `traccoon_*` in allowed_tools.
TRACCOON_TOOLS = [
    _def("traccoon_list_projects", "Projekte auflisten, auf die dein Mensch Zugriff hat "
         "(Key, Name, seine Rolle, ob KI-Zuweisung erlaubt).", {}, []),
    _def("traccoon_list_issues", "Tickets eines Projekts auflisten (nur bei Zugriff). "
         "Optional nach agent_status filtern.",
         {"project_key": {"type": "string"}, "agent_status": {"type": "string"},
          "limit": {"type": "integer"}}, ["project_key"]),
    _def("traccoon_get_issue", "Ein Ticket im Detail (Status, zugewiesener Agent, Plan, Beschreibung).",
         {"key": {"type": "string"}}, ["key"]),
    _def("traccoon_create_issue", "Neues Ticket in einem Projekt anlegen (Mitgliedschaft nötig).",
         {"project_key": {"type": "string"}, "summary": {"type": "string"},
          "description": {"type": "string"}}, ["project_key", "summary"]),
    _def("traccoon_comment", "Kommentar an ein Ticket schreiben.",
         {"key": {"type": "string"}, "text": {"type": "string"}}, ["key", "text"]),
    _def("traccoon_assign_agent", "Einem Ticket einen Agenten (Rolle) zuweisen (KI-Recht nötig).",
         {"key": {"type": "string"}, "role": {"type": "string"}}, ["key", "role"]),
    _def("traccoon_start_planning", "Planung eines Tickets starten (KI-Recht + zugewiesener Agent).",
         {"key": {"type": "string"}}, ["key"]),
    _def("traccoon_approve_plan", "Vorgeschlagenen Plan eines Tickets freigeben (KI-Recht).",
         {"key": {"type": "string"}}, ["key"]),
    _def("traccoon_issue_costs", "Kosten (USD, Tokens) eines Tickets.",
         {"key": {"type": "string"}}, ["key"]),
]
TRACCOON_TOOL_NAMES = {t["function"]["name"] for t in TRACCOON_TOOLS}


async def _user(db: AsyncSession, owner_id: int | None) -> User | None:
    return await db.get(User, owner_id) if owner_id else None


async def _issue_access(db: AsyncSession, user: User, key: str):
    """(Issue, Access, Project) für einen Ticket-Key — oder (None, Fehlertext)."""
    iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
    if iss is None:
        return None, None, f"Ticket '{key}' nicht gefunden."
    project = await db.get(Project, iss.project_id)
    try:
        acc = await build_access(project, user, db)
    except HTTPException:
        return None, None, f"Kein Zugriff auf das Projekt von '{key}'."
    return iss, acc, project


async def call_traccoon_tool(db: AsyncSession, owner_id: int | None, name: str, args: dict) -> str:
    user = await _user(db, owner_id)
    if user is None:
        return "FEHLER: kein Nutzerkontext — Steuerung nicht möglich."

    if name == "traccoon_list_projects":
        if user.global_role == "admin":
            projs = (await db.execute(select(Project).order_by(Project.key))).scalars().all()
        else:
            projs = (await db.execute(select(Project).join(
                ProjectMember, ProjectMember.project_id == Project.id).where(
                ProjectMember.user_id == user.id).order_by(Project.key))).scalars().all()
        if not projs:
            return "Keine zugänglichen Projekte."
        out = []
        for p in projs:
            try:
                a = await build_access(p, user, db)
                out.append(f"- {p.key}: {p.name} (Rolle {a.role.value}, KI-Recht {'ja' if a.ai_assign else 'nein'})")
            except HTTPException:
                pass
        return "\n".join(out)

    if name == "traccoon_list_issues":
        p = (await db.execute(select(Project).where(Project.key == args.get("project_key")))).scalar_one_or_none()
        if p is None:
            return f"Projekt '{args.get('project_key')}' nicht gefunden."
        try:
            await build_access(p, user, db)
        except HTTPException:
            return "Kein Zugriff auf dieses Projekt."
        q = select(Issue).where(Issue.project_id == p.id)
        if args.get("agent_status"):
            q = q.where(Issue.agent_status == args["agent_status"])
        limit = min(int(args.get("limit") or 20), 50)
        rows = (await db.execute(q.order_by(Issue.updated_at.desc()).limit(limit))).scalars().all()
        if not rows:
            return "Keine Tickets."
        return "\n".join(f"- {i.key} [{_v(i.agent_status)}] {i.summary} "
                         f"(Agent: {i.assigned_agent or '—'})" for i in rows)

    if name == "traccoon_get_issue":
        iss, acc, err = await _issue_access(db, user, args.get("key", ""))
        if iss is None:
            return err
        return (f"{iss.key}: {iss.summary}\nagent_status: {_v(iss.agent_status)} · "
                f"hold: {_v(iss.hold_reason)} · Agent: {iss.assigned_agent or '—'}\n"
                f"Beschreibung:\n{(iss.description or '')[:1500]}\n"
                + (f"\nPlan:\n{(iss.plan or '')[:2000]}" if iss.plan else ""))

    if name == "traccoon_create_issue":
        p = (await db.execute(select(Project).where(Project.key == args.get("project_key")))).scalar_one_or_none()
        if p is None:
            return f"Projekt '{args.get('project_key')}' nicht gefunden."
        try:
            await build_access(p, user, db)
        except HTTPException:
            return "Kein Zugriff auf dieses Projekt."
        t = (await db.execute(select(IssueType).where(IssueType.project_id == p.id)
                              .order_by(IssueType.order))).scalars().first()
        s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == p.id)
                              .order_by(WorkflowStatus.order))).scalars().first()
        counter = (await db.execute(select(IssueCounter).where(
            IssueCounter.project_id == p.id).with_for_update())).scalar_one_or_none()
        if t is None or s is None or counter is None:
            return "Projekt ist nicht vollständig konfiguriert (Typ/Status/Zähler)."
        counter.last_number += 1
        n = counter.last_number
        iss = Issue(project_id=p.id, number=n, key=f"{p.key}-{n}"[:50], type_id=t.id, status_id=s.id,
                    summary=(args.get("summary") or "")[:500], description=args.get("description", ""),
                    reporter_id=user.id, rank=f"{n:08d}")
        db.add(iss)
        await db.commit()
        return f"Ticket {iss.key} angelegt."

    if name == "traccoon_comment":
        iss, acc, err = await _issue_access(db, user, args.get("key", ""))
        if iss is None:
            return err
        from ..services.comments import apply_user_comment
        await apply_user_comment(db, iss, args.get("text", ""), user.id, "Assistent")
        return f"Kommentar an {iss.key} gespeichert."

    if name == "traccoon_assign_agent":
        iss, acc, err = await _issue_access(db, user, args.get("key", ""))
        if iss is None:
            return err
        if not acc.ai_assign:
            return f"Kein KI-Recht (ai_assign) im Projekt von {iss.key}."
        iss.assigned_agent = (args.get("role") or "").strip()
        iss.assigned_by_user_id = user.id
        iss.assigned_at = _now()
        await db.commit()
        return f"{iss.key}: Agent '{iss.assigned_agent}' zugewiesen."

    if name in ("traccoon_start_planning", "traccoon_approve_plan"):
        iss, acc, err = await _issue_access(db, user, args.get("key", ""))
        if iss is None:
            return err
        if not acc.ai_assign:
            return f"Kein KI-Recht (ai_assign) im Projekt von {iss.key}."
        from ..services.dispatcher import sync_board_status
        if name == "traccoon_start_planning":
            if iss.assigned_agent is None:
                return f"{iss.key}: kein Agent zugewiesen."
            iss.agent_status = TicketAgentStatus.planning
            iss.hold_reason = None
            iss.cap_baseline_run_id = (await db.execute(
                select(func.max(Run.id)).where(Run.issue_id == iss.id))).scalar()
            await sync_board_status(db, iss)
            await db.commit()
            return f"{iss.key}: Planung gestartet."
        else:
            if iss.agent_status != TicketAgentStatus.plan_review or not iss.plan:
                return f"{iss.key}: kein Plan zur Freigabe (Status {iss.agent_status})."
            iss.agent_status = TicketAgentStatus.approved
            iss.hold_reason = None
            iss.cap_baseline_run_id = (await db.execute(
                select(func.max(Run.id)).where(Run.issue_id == iss.id))).scalar()
            await sync_board_status(db, iss)
            await db.commit()
            return f"{iss.key}: Plan freigegeben."

    if name == "traccoon_issue_costs":
        iss, acc, err = await _issue_access(db, user, args.get("key", ""))
        if iss is None:
            return err
        row = (await db.execute(select(
            func.coalesce(func.sum(CostEntry.cost_usd), 0.0),
            func.coalesce(func.sum(CostEntry.input_tokens), 0),
            func.coalesce(func.sum(CostEntry.output_tokens), 0),
        ).where(CostEntry.issue_id == iss.id))).one()
        return f"{iss.key}: ${row[0]:.4f} (in {row[1]} / out {row[2]} Tokens)."

    return f"FEHLER: unbekanntes Steuer-Tool '{name}'."
