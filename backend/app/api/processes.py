"""Process administration: the cross-cutting view of everything running as a flow.

Since all flows are graphs, the knowledge about them is spread over sets, project copies,
versions and running instances. The endpoints here bring that together and answer the four
questions one asks an administration:

* `/processes/slots`    - what is the default, and who deviates from it?
* `/processes/running`  - what is running right now, and where is something stuck?
* `/processes/triggers` - what starts which flow?
* Rolling back sits with the versions (`api/workflows.py`), because that is where it belongs.

Visibility: the default set is readable for everyone (it explains how Traccoon works) and is
changed only by the admin. Operation and triggers show exclusively projects the requester
has access to; otherwise a flow would reveal the names of foreign projects.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Fehler
from ..db import get_session
from ..models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowTokenState,
)
from ..models.project import Project
from ..models.user import User
from ..models.workflow import (
    WorkflowDefinition, WorkflowInstance, WorkflowSet, WorkflowStepRun, WorkflowToken,
    WorkflowVersion,
)
from ..services import events as ev
from ..services import workflow_sets as sets
from .deps import build_access, get_current_user

router = APIRouter(tags=["processes"])


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """Give a timestamp a zone.

    Postgres returns zone aware values, SQLite (tests) zone naive ones; without this
    alignment every difference fails with "can't subtract offset-naive and offset-aware".
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


async def _sichtbare_projekte(db: AsyncSession, user: User) -> dict[int, Project]:
    """Projects this user may see: determined once, only read afterwards."""
    alle = (await db.execute(select(Project))).scalars().all()
    out: dict[int, Project] = {}
    for p in alle:
        try:
            await build_access(p, user, db)
        except Exception:  # noqa: BLE001 - 403/404 simply means: not visible
            continue
        out[p.id] = p
    return out


# ── Default set and deviations ───────────────────────────────────────────────

class DeviationOut(BaseModel):
    project_id: int
    project_key: str
    project_name: str
    definition_id: int
    published: bool


class SlotOverviewOut(BaseModel):
    slot: str
    name: str
    description: str
    subject_kind: str
    definition_id: int | None = None
    definition_name: str | None = None
    version: int | None = None
    published: bool = False
    updated_at: dt.datetime | None = None
    # Projects with their own copy of this slot: those no longer follow the set.
    abweichungen: list[DeviationOut] = []


@router.get("/processes/slots", response_model=list[SlotOverviewOut])
async def slot_overview(
    set_id: int | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Occupancy of a set including the projects that deviate from it.

    Without `set_id` the global default, which is the basis of all projects and was reachable
    only over the API so far.
    """
    if set_id is None:
        s = (await db.execute(select(WorkflowSet).where(
            WorkflowSet.key == sets.BUILTIN_SET_KEY))).scalars().first()
        if s is None:
            raise Fehler(status.HTTP_404_NOT_FOUND, "err.no_global_default_set",
                         "No global default set")
    else:
        s = await db.get(WorkflowSet, set_id)
        if s is None:
            raise Fehler(status.HTTP_404_NOT_FOUND, "err.process_set_not_found",
                         "Process set not found")

    visible = await _sichtbare_projekte(db, user)
    # Project-owned copies per slot (not archived): those are exactly the deviations.
    kopien = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.slot.isnot(None),
        WorkflowDefinition.project_id.isnot(None),
        WorkflowDefinition.archived_at.is_(None),
    ))).scalars().all()

    out: list[SlotOverviewOut] = []
    for slot, meta in sets.SLOT_META.items():
        d = await sets.set_definition(db, s.id, slot)
        version = await db.get(WorkflowVersion, d.current_version_id) if d and d.current_version_id else None
        abw = [
            DeviationOut(
                project_id=k.project_id, project_key=visible[k.project_id].key,
                project_name=visible[k.project_id].name, definition_id=k.id,
                published=bool(k.current_version_id),
            )
            for k in kopien if k.slot == slot and k.project_id in visible
        ]
        out.append(SlotOverviewOut(
            slot=slot, name=meta["name"], description=meta["description"],
            subject_kind=meta["subject_kind"],
            definition_id=d.id if d else None, definition_name=d.name if d else None,
            version=version.version if version else None,
            published=bool(version), updated_at=d.updated_at if d else None,
            abweichungen=sorted(abw, key=lambda a: a.project_key),
        ))
    return out


# ── Operation: what runs, what is stuck ──────────────────────────────────────

# From when does a waiting step count as "stuck"? If a flow waits for a human, that is
# normal, but after a day without a stir one wants to see it anyway.
HANGS_AB_HOURS = 24


class LaufOut(BaseModel):
    id: int
    definition_id: int
    definition_name: str
    slot: str | None = None
    project_id: int | None = None
    project_key: str | None = None
    subject_kind: WorkflowSubjectKind
    # What the flow hangs off: ticket key respectively unit identifier.
    subject_ref: str | None = None
    status: WorkflowInstanceStatus
    # Current stop: node label and what is being waited for.
    node_label: str | None = None
    waiting_for: str | None = None
    seit: dt.datetime | None = None
    hours: float | None = None
    hangs: bool = False
    error: str | None = None
    started_at: dt.datetime


def _label(graph: dict, node_id: str | None) -> str | None:
    if not node_id:
        return None
    for n in graph.get("nodes") or []:
        if n.get("id") == node_id:
            cfg = (n.get("data") or {}).get("config") or {}
            return cfg.get("label") or node_id
    return node_id


@router.get("/processes/running", response_model=list[LaufOut])
async def laufende(
    include_done: bool = False, only_stuck: bool = False, limit: int = 200,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """All flows across the projects, with the point they are standing at right now.

    `only_stuck=true` shows only what needs attention: failed instances and those waiting at
    the same step for longer than a day.
    """
    q = select(WorkflowInstance)
    if not include_done:
        # Open is everything that is not finished or aborted; `waiting` explicitly belongs to
        # that: a flow waiting for a human is the normal case and exactly what an operations
        # view has to show.
        q = q.where(WorkflowInstance.status.notin_(
            [WorkflowInstanceStatus.completed, WorkflowInstanceStatus.cancelled]))
    rows = (await db.execute(q.order_by(WorkflowInstance.id.desc()).limit(limit))).scalars().all()

    visible = await _sichtbare_projekte(db, user)
    now = _now()
    out: list[LaufOut] = []
    for inst in rows:
        if inst.project_id is not None and inst.project_id not in visible:
            continue
        d = await db.get(WorkflowDefinition, inst.definition_id)
        version = await db.get(WorkflowVersion, inst.version_id)
        graph = (version.graph if version else None) or {}

        token = (await db.execute(select(WorkflowToken).where(
            WorkflowToken.instance_id == inst.id,
            WorkflowToken.state.in_([WorkflowTokenState.waiting, WorkflowTokenState.active]),
        ).order_by(WorkflowToken.id.desc()))).scalars().first()
        # How long has it been standing? The last entered step is more honest than the token,
        # whose timestamp is touched by advancing within the same node as well.
        step = (await db.execute(select(WorkflowStepRun).where(
            WorkflowStepRun.instance_id == inst.id,
        ).order_by(WorkflowStepRun.id.desc()))).scalars().first()
        seit = _aware((step.entered_at if step else None) or inst.started_at)
        hours = round((now - seit).total_seconds() / 3600, 1) if seit else None
        hangs = (inst.status == WorkflowInstanceStatus.failed
                  or bool(hours and hours >= HANGS_AB_HOURS))
        if only_stuck and not hangs:
            continue

        ref = None
        if inst.issue_id:
            from ..models.ticket import Issue
            issue = await db.get(Issue, inst.issue_id)
            ref = issue.key if issue else None
        elif inst.hardware_asset_id:
            ref = f"HW-{inst.hardware_asset_id}"

        out.append(LaufOut(
            id=inst.id, definition_id=inst.definition_id,
            definition_name=d.name if d else "—", slot=d.slot if d else None,
            project_id=inst.project_id,
            project_key=visible[inst.project_id].key if inst.project_id in visible else None,
            subject_kind=inst.subject_kind, subject_ref=ref, status=inst.status,
            node_label=_label(graph, token.node_id if token else (step.node_id if step else None)),
            waiting_for=token.waiting_for if token else None,
            seit=seit, hours=hours, hangs=hangs,
            error=inst.error, started_at=inst.started_at,
        ))
    return out


# ── Triggers: what starts which flow ─────────────────────────────────────────

class TriggerOut(BaseModel):
    definition_id: int
    definition_name: str
    slot: str | None = None
    project_id: int | None = None
    project_key: str | None = None
    # event | webhook | job | subflow | manual
    kind: str
    # Ereignis-Name, Webhook-Route bzw. Job-Name.
    source: str
    label: str
    # Only with event triggers: restriction to one project.
    only_project_id: int | None = None
    enabled: bool = True


@router.get("/processes/triggers", response_model=list[TriggerOut])
async def trigger(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """What starts a flow: an event, a webhook, a job or another flow.

    What is read are the published graphs (start nodes) and the references in webhooks and
    jobs. There is deliberately no trigger table of its own: the graph is the truth and can
    therefore not drift apart from any index.
    """
    from ..models.ops import Job, WebhookSub

    visible = await _sichtbare_projekte(db, user)
    ereignis_label = dict(ev.BUILTIN_EVENTS)

    defs = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.archived_at.is_(None)))).scalars().all()
    known = {d.id: d for d in defs
               if d.project_id is None or d.project_id in visible}

    def header(d: WorkflowDefinition) -> dict:
        return {
            "definition_id": d.id, "definition_name": d.name, "slot": d.slot,
            "project_id": d.project_id,
            "project_key": visible[d.project_id].key if d.project_id in visible else None,
        }

    out: list[TriggerOut] = []

    # 1) Event triggers on the start node of the published version.
    for d in known.values():
        if not d.current_version_id:
            continue
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = ev.trigger_of(version.graph if version else {})
        if not t or not t.get("event"):
            continue
        name = str(t["event"])
        out.append(TriggerOut(
            **header(d), kind="event", source=name,
            label=ereignis_label.get(name, name),
            only_project_id=t.get("project_id") or None,
            enabled=bool(d.enabled),
        ))

    # 2) Webhooks and jobs that point directly at a definition.
    for hook in (await db.execute(select(WebhookSub).where(
            WebhookSub.workflow_definition_id.isnot(None)))).scalars().all():
        d = known.get(hook.workflow_definition_id)
        if d is None:
            continue
        out.append(TriggerOut(**header(d), kind="webhook", source=hook.route,
                                label=f"Webhook /{hook.route}"))

    for job in (await db.execute(select(Job).where(
            Job.workflow_definition_id.isnot(None)))).scalars().all():
        d = known.get(job.workflow_definition_id)
        if d is None:
            continue
        out.append(TriggerOut(**header(d), kind="job", source=job.name,
                                label=f"Job „{job.name}“", enabled=bool(job.enabled)))

    # 3) Calls from other flows (subflow nodes); otherwise a flow would look triggerless
    #    although another one calls it.
    for d in known.values():
        if not d.current_version_id:
            continue
        version = await db.get(WorkflowVersion, d.current_version_id)
        for n in ((version.graph if version else {}) or {}).get("nodes") or []:
            if n.get("type") != "subflow":
                continue
            slot = ((n.get("data") or {}).get("config") or {}).get("slot")
            if not slot:
                continue
            target = next((z for z in known.values() if z.slot == slot), None)
            if target is None:
                continue
            out.append(TriggerOut(**header(target), kind="subflow", source=d.name,
                                    label=f"Aufruf aus „{d.name}“"))

    # 4) Everything without a trigger: runs only when a human (or code) starts it.
    mit_trigger = {a.definition_id for a in out}
    for d in known.values():
        if d.id in mit_trigger or not d.current_version_id:
            continue
        out.append(TriggerOut(**header(d), kind="manual", source="",
                                label="Nur manuell bzw. aus dem Programm",
                                enabled=bool(d.enabled)))

    return sorted(out, key=lambda a: (a.kind != "event", a.definition_name, a.label))


# ── Event catalog (for the selection in the editor and the overview) ─────────

class EreignisOut(BaseModel):
    event: str
    label: str
    listeners: int


@router.get("/processes/events", response_model=list[EreignisOut])
async def ereignisse(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """All known events with the number of flows listening for them."""
    defs = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.archived_at.is_(None),
        WorkflowDefinition.current_version_id.isnot(None),
    ))).scalars().all()
    counter: dict[str, int] = {}
    for d in defs:
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = ev.trigger_of(version.graph if version else {})
        if t and t.get("event"):
            counter[str(t["event"])] = counter.get(str(t["event"]), 0) + 1
    return [EreignisOut(event=e, label=l, listeners=counter.get(e, 0))
            for e, l in ev.BUILTIN_EVENTS]
