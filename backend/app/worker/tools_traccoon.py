"""Native control tools: the assistant operates Traccoon IN THE NAME of its human and
STRICTLY within their rights (build_access per project). Generic, with no personal data in
the code. The return value of every tool is terse text for the agent.
"""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException
from sqlalchemy import func, or_, select
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


# Deny by default: the assistant only gets these with `traccoon_*` in allowed_tools.
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
    _def("traccoon_notify_human",
         "Melde deinem Menschen etwas, das er wissen MUSS oder ausdrücklich wissen WILL "
         "(Frist, Geldbetrag, Entscheidung, Störung, etwas das er beantworten muss). "
         "Ohne diesen Aufruf bleibt dein Lauf still — ein erledigtes „nichts zu tun\" oder "
         "eine reine Ablage sind KEIN Grund zu melden. Sparsam einsetzen.",
         {"title": {"type": "string", "description": "Eine Zeile, worum es geht"},
          "text": {"type": "string", "description": "Was der Mensch wissen muss"},
          "urgency": {"type": "string", "description": "normal (Standard) oder high"}},
         ["title"]),
    _def("traccoon_list_destinations",
         "Freigegebene externe Ziele auflisten (Name, Zweck, Basis-URL). Zugangsdaten sieht "
         "niemand — sie werden beim Aufruf serverseitig gesetzt.", {}, []),
    _def("traccoon_list_jobs",
         "Geplante Jobs deines Menschen auflisten (Nummer, Name, Zeitplan, Agent, an/aus, "
         "letzter Lauf). Erst hier nachsehen, bevor du einen Job für nicht vorhanden hältst.",
         {}, []),
    _def("traccoon_get_job",
         "Ein Job im Detail: Prompt, Parameter, Zeitplan, Meldeweg und die letzten Läufe.",
         {"job_id": {"type": "integer"}}, ["job_id"]),
    _def("traccoon_job_templates",
         "Verfügbare Job-Vorlagen samt ihrer Parameter (z. B. 'recherche-digest' für einen "
         "wiederkehrenden Themen-Rückblick).", {}, []),
    _def("traccoon_create_job",
         "Einen wiederkehrenden Job anlegen. Am besten über `template` + `params` — dann "
         "kommen Prompt und Voreinstellungen aus der Vorlage. Zeitplan: type 'cron' "
         "(z. B. '0 6 * * *', UTC), 'interval' (Sekunden) oder 'once' (ISO-Zeit).",
         {"name": {"type": "string"},
          "template": {"type": "string", "description": "Schlüssel einer Vorlage"},
          "params": {"type": "object", "description": "Parameter der Vorlage bzw. Werte für "
                     "die {{platzhalter}} im Prompt"},
          "prompt": {"type": "string", "description": "nur ohne Vorlage"},
          "agent": {"type": "string"}, "type": {"type": "string"},
          "schedule": {"type": "string"},
          "enabled": {"type": "boolean", "description": "Standard: an"}},
         ["name"]),
    _def("traccoon_update_job",
         "Einen Job ändern — auch an-/abschalten (enabled) oder Parameter nachziehen. "
         "Nur die übergebenen Felder werden angefasst.",
         {"job_id": {"type": "integer"}, "name": {"type": "string"},
          "prompt": {"type": "string"}, "params": {"type": "object"},
          "agent": {"type": "string"}, "type": {"type": "string"},
          "schedule": {"type": "string"}, "enabled": {"type": "boolean"},
          "notify_mode": {"type": "string", "description": "always|on_output|on_error|never"}},
         ["job_id"]),
    _def("traccoon_run_job", "Einen Job sofort ausführen (zusätzlich zum Zeitplan).",
         {"job_id": {"type": "integer"}}, ["job_id"]),
    _def("traccoon_list_workflows",
         "Veröffentlichte Prozesse (Workflows) auflisten, die dein Mensch starten darf — "
         "projektlose und die seiner Projekte. Liefert id, key, Name und Gegenstandsart.",
         {"project_key": {"type": "string", "description": "optional: nur dieses Projekt"}}, []),
    _def("traccoon_start_workflow",
         "Eine Instanz eines veröffentlichten Prozesses starten. `context` sind die "
         "Startwerte des Graphen (frei belegbares Objekt). Ein Prozess mit Gegenstand "
         "'issue' braucht `issue_key`.",
         {"workflow_id": {"type": "integer", "description": "id aus traccoon_list_workflows"},
          "issue_key": {"type": "string", "description": "nur bei Prozessen auf Tickets"},
          "context": {"type": "object", "description": "Startwerte für den Graphen"}},
         ["workflow_id"]),
    _def("traccoon_http_call",
         "Ein freigegebenes Ziel aufrufen. Basis-URL und Anmeldung kommen aus dem Ziel; du "
         "gibst nur Methode, Pfad-Ergänzung, Query, Kopfzeilen und Body an.",
         {"destination": {"type": "string", "description": "Name des Ziels"},
          "method": {"type": "string", "description": "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"},
          "path": {"type": "string", "description": "Ergänzung der Basis-URL, z. B. /api/v2/orders"},
          "query": {"type": "object", "description": "Query-Parameter"},
          "headers": {"type": "object", "description": "zusätzliche Kopfzeilen"},
          "body": {"description": "JSON-Objekt/Liste oder Text"}},
         ["destination"]),
]
TRACCOON_TOOL_NAMES = {t["function"]["name"] for t in TRACCOON_TOOLS}

# Control tools are exempt from the assistant gate, because they only act within the rights
# of the human anyway. For jobs that does not apply: a job is a PERMANENT, self-acting and
# chargeable arrangement, and a human should have confirmed it once (the gate then remembers
# "always"). Reading stays free.
# `traccoon_start_workflow` is included for the same reason: a process can trigger agent
# runs, approvals and calls to the outside, which is not something an agent sets off unnoticed.
# Auflisten bleibt frei.
TRACCOON_GATED_TOOLS = {"traccoon_create_job", "traccoon_update_job", "traccoon_run_job",
                        "traccoon_start_workflow"}


async def _user(db: AsyncSession, owner_id: int | None) -> User | None:
    return await db.get(User, owner_id) if owner_id else None


async def _issue_access(db: AsyncSession, user: User, key: str):
    """(Issue, Access, Project) for a ticket key, or (None, error text)."""
    iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
    if iss is None:
        return None, None, f"Ticket '{key}' nicht gefunden."
    project = await db.get(Project, iss.project_id)
    try:
        acc = await build_access(project, user, db)
    except HTTPException:
        return None, None, f"Kein Zugriff auf das Projekt von '{key}'."
    return iss, acc, project


_JOB_FIELDS = ("name", "prompt", "agent", "type", "schedule", "enabled", "notify_mode")


async def _job_tool(db: AsyncSession, user: User, name: str, args: dict) -> str:
    """Read and maintain the jobs of the human, strictly their own.

    The reason: the assistant could not see jobs and considered a job that had long been
    moved non-existent. Reading is free, writing goes over the gate (TRACCOON_GATED_TOOLS).
    """
    from ..models.ops import Job, JobRun
    from ..services.job_params import open_placeholder, parameter
    from ..services.job_templates import JOB_TEMPLATES, apply, listing

    async def _job(jid) -> Job | None:
        j = await db.get(Job, int(jid or 0))
        # Foreign jobs simply do not exist for the assistant, not even as "forbidden".
        return j if j is not None and j.user_id == user.id else None

    if name == "traccoon_job_templates":
        return "\n".join(
            f"- {v['key']}: {v['label']} — {v['beschreibung']}\n"
            f"  Parameter: {', '.join(v['params'])}" for v in listing()) or "Keine Vorlagen."

    if name == "traccoon_list_jobs":
        rows = (await db.execute(select(Job).where(Job.user_id == user.id)
                                 .order_by(Job.id))).scalars().all()
        if not rows:
            return "Keine geplanten Jobs."
        return "\n".join(
            f"- #{j.id} {j.name} [{'an' if j.enabled else 'AUS'}"
            f"{', pausiert' if j.paused else ''}] {j.type}:{j.schedule} · {j.kind}"
            f" · Agent {j.agent or '—'}"
            f" · zuletzt {j.last_run_at.strftime('%Y-%m-%d %H:%M') if j.last_run_at else 'nie'}"
            for j in rows)

    if name == "traccoon_get_job":
        j = await _job(args.get("job_id"))
        if j is None:
            return "Job nicht gefunden."
        runs = (await db.execute(select(JobRun).where(JobRun.job_id == j.id)
                                   .order_by(JobRun.id.desc()).limit(5))).scalars().all()
        p = parameter(j.args)
        return (f"#{j.id} {j.name}\n"
                f"Zeitplan: {j.type}:{j.schedule} · {'an' if j.enabled else 'AUS'} · "
                f"Art {j.kind} · Agent {j.agent or '—'} · Meldung {j.notify_mode}\n"
                + (f"Parameter: {p}\n" if p else "")
                + (f"Offene Platzhalter (ohne Wert!): {', '.join(o)}\n"
                   if (o := open_placeholder(j.prompt, j.args)) else "")
                + f"Prompt:\n{(j.prompt or '')[:2000]}\n"
                + "Letzte Läufe: " + (", ".join(
                    f"{r.started_at:%Y-%m-%d %H:%M} {r.status}" for r in runs) or "keine"))

    if name == "traccoon_create_job":
        fields: dict = {}
        if args.get("template"):
            try:
                fields = apply(str(args["template"]), args.get("params") or {})
            except KeyError:
                return (f"Vorlage '{args['template']}' gibt es nicht. Verfügbar: "
                        f"{', '.join(JOB_TEMPLATES)}.")
        elif args.get("params"):
            fields["args"] = dict(args["params"])
        for f in _JOB_FIELDS:
            if args.get(f) is not None:
                fields[f] = args[f]
        if not (fields.get("prompt") or "").strip():
            return "Ohne Prompt (oder Vorlage) kein Job."
        fields.setdefault("kind", "prompt")
        fields.setdefault("type", "cron")
        fields.setdefault("schedule", "0 6 * * *")
        fields.setdefault("agent", "assistent")
        fields["name"] = str(args.get("name") or "Namenloser Job")[:255]
        # The message goes to the same chat as everything else from it.
        fields.setdefault("notify_chat", user.telegram_chat_id)
        j = Job(user_id=user.id, **fields)
        db.add(j)
        await db.commit()
        await db.refresh(j)
        open_ones = open_placeholder(j.prompt, j.args)
        return (f"Job #{j.id} '{j.name}' angelegt ({j.type}:{j.schedule}, "
                f"{'an' if j.enabled else 'aus'})."
                + (f" ACHTUNG: Platzhalter ohne Wert: {', '.join(open_ones)}." if open_ones else ""))

    if name == "traccoon_update_job":
        j = await _job(args.get("job_id"))
        if j is None:
            return "Job nicht gefunden."
        changed = []
        for f in _JOB_FIELDS:
            if args.get(f) is not None and getattr(j, f) != args[f]:
                setattr(j, f, args[f])
                changed.append(f)
        if args.get("params"):
            # Update, do not replace: otherwise a job loses all its other parameters when
            # one value is changed.
            j.args = {**parameter(j.args), **args["params"]}
            changed.append("params")
        if not changed:
            return f"Job #{j.id}: nichts zu ändern."
        if "enabled" in changed and j.enabled:
            j.paused = False
        await db.commit()
        return f"Job #{j.id} geändert: {', '.join(changed)}."

    if name == "traccoon_run_job":
        j = await _job(args.get("job_id"))
        if j is None:
            return "Job nicht gefunden."
        jr = JobRun(job_id=j.id, status="running")
        db.add(jr)
        j.last_run_at = _now()
        await db.flush()
        # One way for all kinds (as in the schedule and in the API).
        from ..services.scheduler import run_job_kind
        await run_job_kind(db, j, jr)
        await db.commit()
        return (f"Job #{j.id} '{j.name}' ({j.kind}) ausgeführt: {jr.status}"
                + (f" — {jr.output[:500]}" if jr.output else "")
                + (f" — FEHLER: {jr.error[:500]}" if jr.error else ""))

    return f"FEHLER: unbekanntes Job-Tool '{name}'."


async def _workflow_tool(db: AsyncSession, user: User, name: str, args: dict) -> str:
    """List and start processes, within the rights of the human, not of the agent.

    Until now an agent could not trigger a workflow at all: job and webhook can, but the tool
    was missing. Access as in api/workflows.py: project-less processes for every logged-in
    user, project bound ones from membership (member) on.
    """
    from ..models.enums import ProjectRole
    from ..models.workflow import WorkflowDefinition
    from ..services.workflow_engine import start_workflow

    async def _allowed(d: WorkflowDefinition) -> bool:
        """May this human start this process?"""
        if d.project_id is None:
            return True
        project = await db.get(Project, d.project_id)
        if project is None:
            return False
        try:
            acc = await build_access(project, user, db)
        except HTTPException:
            return False
        return acc.has_role(ProjectRole.member)

    if name == "traccoon_list_workflows":
        q = select(WorkflowDefinition).where(
            WorkflowDefinition.archived_at.is_(None),
            WorkflowDefinition.enabled.is_(True),
            # Without a published version there is nothing to start; drafts stay silent.
            WorkflowDefinition.current_version_id.is_not(None))
        if args.get("project_key"):
            p = (await db.execute(select(Project).where(
                Project.key == args["project_key"]))).scalar_one_or_none()
            if p is None:
                return f"Projekt '{args['project_key']}' nicht gefunden."
            q = q.where(or_(WorkflowDefinition.project_id == p.id,
                            WorkflowDefinition.project_id.is_(None)))
        rows = (await db.execute(q.order_by(WorkflowDefinition.id))).scalars().all()
        lines = []
        for d in rows:
            if not await _allowed(d):
                continue
            project_row = "projektlos"
            if d.project_id is not None:
                p = await db.get(Project, d.project_id)
                project_row = p.key if p else f"Projekt {d.project_id}"
            lines.append(f"- id {d.id} · {d.key}: {d.name} ({project_row}, "
                          f"Gegenstand {d.subject_kind.value if hasattr(d.subject_kind, 'value') else d.subject_kind})")
        return "\n".join(lines) or "Keine startbaren Prozesse."

    if name == "traccoon_start_workflow":
        d = await db.get(WorkflowDefinition, int(args.get("workflow_id") or 0))
        if d is None or d.archived_at is not None:
            return "Prozess nicht gefunden."
        if not await _allowed(d):
            return "Kein Zugriff auf diesen Prozess."
        if not d.enabled:
            return f"Prozess '{d.key}' ist abgeschaltet."
        if d.current_version_id is None:
            return f"Prozess '{d.key}' hat keine veröffentlichte Version."
        sk = d.subject_kind.value if hasattr(d.subject_kind, "value") else str(d.subject_kind)
        issue_id = None
        if args.get("issue_key"):
            iss, _acc, error = await _issue_access(db, user, args["issue_key"])
            if iss is None:
                return error
            issue_id = iss.id
        elif sk == "issue":
            return f"Prozess '{d.key}' läuft auf einem Ticket — issue_key angeben."
        context = args.get("context")
        try:
            inst = await start_workflow(
                db, d, subject_kind=d.subject_kind, issue_id=issue_id,
                context=context if isinstance(context, dict) else {},
                actor_id=user.id, source=f"agent:{user.id}",
            )
        except ValueError as e:
            return f"FEHLER: {e}"
        return (f"Prozess '{d.key}' gestartet — Instanz #{inst.id}, Status "
                f"{inst.status.value if hasattr(inst.status, 'value') else inst.status}. "
                "Wartende Schritte (Freigaben, Aufgaben) laufen ohne dich weiter.")

    return f"FEHLER: unbekanntes Workflow-Tool '{name}'."


async def call_traccoon_tool(db: AsyncSession, owner_id: int | None, name: str, args: dict,
                             assistant_task_id: int | None = None) -> str:
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
        # Assigning means starting, exactly as over the interface (`/issues/{key}/assign-agent`).
        # Without these lines the assistant only set fields, and the ticket lay there with an
        # agent and a status without a process running (ABC-32 on 2026-08-07).
        from ..services.lifecycle_flow import start_lifecycle
        inst = await start_lifecycle(db, iss, user.id, advance_now=False,
                                     entry="exec" if iss.plan else "plan")
        await db.commit()
        if inst is None:
            return (f"{iss.key}: Agent '{iss.assigned_agent}' zugewiesen — aber KEIN Prozess "
                    "gestartet (kein veröffentlichter Lebenszyklus für dieses Projekt).")
        return f"{iss.key}: Agent '{iss.assigned_agent}' zugewiesen, Lebenszyklus läuft an."

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
            from ..services.artifacts import set_ticket_status
            await set_ticket_status(db, iss, TicketAgentStatus.planning)
            iss.hold_reason = None
            iss.cap_baseline_run_id = (await db.execute(
                select(func.max(Run.id)).where(Run.issue_id == iss.id))).scalar()
            await sync_board_status(db, iss)
            # The status alone plans nothing. Advancing does NOT happen here: `advance`
            # belongs in the backend process, whose 30 s tick finds the fresh token; out of
            # the worker the watchers of the following steps hung in the wrong process.
            from ..services.lifecycle_flow import start_lifecycle
            inst = await start_lifecycle(db, iss, user.id, advance_now=False, entry="plan",
                                         restart=True)
            await db.commit()
            if inst is None:
                return (f"{iss.key}: Status auf Planung gesetzt, aber KEIN Prozess gestartet "
                        "(kein veröffentlichter Lebenszyklus für dieses Projekt).")
            return f"{iss.key}: Planung gestartet (Prozess-Instanz {inst.id})."
        else:
            if iss.agent_status != TicketAgentStatus.plan_review or not iss.plan:
                return f"{iss.key}: kein Plan zur Freigabe (Status {iss.agent_status})."
            from ..services.artifacts import set_ticket_status
            await set_ticket_status(db, iss, TicketAgentStatus.approved)
            iss.hold_reason = None
            iss.cap_baseline_run_id = (await db.execute(
                select(func.max(Run.id)).where(Run.issue_id == iss.id))).scalar()
            await sync_board_status(db, iss)
            # The approval is a STEP in the process, not a field on the ticket: the graph
            # stands on `approve_plan` and waits. Setting only the status made the ticket
            # look approved while nobody started.
            from ..services.lifecycle_flow import decide_open_approval
            decided = await decide_open_approval(db, iss, "approved", user.id)
            await db.commit()
            if not decided:
                return (f"{iss.key}: Status auf freigegeben gesetzt — im Prozess wartete "
                        "aber keine Genehmigung (läuft dort gerade etwas anderes?).")
            return f"{iss.key}: Plan freigegeben, der Prozess läuft weiter."

    if name == "traccoon_notify_human":
        # An explicit message to the human. It is the ONLY regular way to trigger a Telegram
        # or bell message out of an assistant run; the closing report stays silent otherwise
        # (exceptions: errors and chat).
        from ..models.notification import Notification
        title = str(args.get("title") or "").strip() or "Hinweis deines Assistenten"
        urgent = str(args.get("urgency") or "").lower() == "high"
        db.add(Notification(
            user_id=user.id, kind="assistant",
            title=(("❗ " if urgent else "") + title)[:200],
            body=str(args.get("text") or "")[:4000],
            chat_id=user.telegram_chat_id))
        if assistant_task_id:
            from ..models.assistant import AssistantTask
            t = await db.get(AssistantTask, assistant_task_id)
            if t is not None:
                t.notified = True
        await db.commit()
        return "Gemeldet."

    if name == "traccoon_list_destinations":
        from ..services import destinations as dests
        rows = await dests.visible(db, owner_id=user.id, agents_only=True)
        if not rows:
            return ("Keine für KI-Agenten freigegebenen Ziele. Ein Mensch muss ein Ziel anlegen "
                    "und dort „für Agenten freigeben\" setzen.")
        return "\n".join(
            f"- {d.name}: {d.label or d.description or '—'} → {d.base_url}" for d in rows)

    if name == "traccoon_http_call":
        from ..services import destinations as dests
        try:
            res = await dests.call_by_name(
                db, str(args.get("destination") or ""), owner_id=user.id, agents_only=True,
                method=str(args.get("method") or "GET"), path=str(args.get("path") or ""),
                query=args.get("query") or {}, headers=args.get("headers") or {},
                body=args.get("body"))
        except Exception as e:  # noqa: BLE001
            return f"FEHLER: {e}"
        await db.commit()   # last_used_at / OAuth-Token-Cache festschreiben
        header = f"{res['method']} {res['url']} → HTTP {res['status_code']}"
        # The limit was set by the destination (ABC-31): do NOT truncate again flatly here,
        # because otherwise an agent would get only the beginning of a deliberately large
        # answer and would plan on truncated JSON without the cut being noticeable.
        max_chars = int(res.get("max_chars") or 4000)
        content = res.get("text")
        if content is None and "json" in res:
            import json as _json
            content = _json.dumps(res["json"], ensure_ascii=False)
        content = content or ""
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n… ABGESCHNITTEN bei {max_chars} Zeichen."
        return f"{header}\n{content}".strip()

    if name in ("traccoon_list_jobs", "traccoon_get_job", "traccoon_job_templates",
                "traccoon_create_job", "traccoon_update_job", "traccoon_run_job"):
        return await _job_tool(db, user, name, args)

    if name in ("traccoon_list_workflows", "traccoon_start_workflow"):
        return await _workflow_tool(db, user, name, args)

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
