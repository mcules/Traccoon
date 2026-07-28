"""Prozess-Verwaltung — die übergreifende Sicht auf alles, was als Ablauf läuft.

Seit alle Abläufe Graphen sind, verteilt sich das Wissen darüber auf Sätze, Projekt-Kopien,
Versionen und laufende Instanzen. Die Endpunkte hier führen das zusammen und beantworten die
vier Fragen, die man an eine Verwaltung stellt:

* `/processes/slots`    — was ist der Standard, und wer weicht davon ab?
* `/processes/running`  — was läuft gerade, und wo hängt etwas?
* `/processes/triggers` — was startet welchen Ablauf?
* Zurückrollen liegt bei den Versionen (`api/workflows.py`), weil es dort hingehört.

Sichtbarkeit: der Standard-Satz ist für alle lesbar (er erklärt, wie Traccoon arbeitet),
geändert wird er nur vom Admin. Betrieb und Auslöser zeigen ausschließlich Projekte, auf die
der Anfragende Zugriff hat — ein Ablauf verrät sonst Namen fremder Projekte.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


def _aware(wert: dt.datetime | None) -> dt.datetime | None:
    """Zeitstempel mit Zone versehen.

    Postgres gibt zonenbehaftete Werte zurück, SQLite (Tests) zonenlose — ohne diese
    Angleichung scheitert jede Differenz mit „can't subtract offset-naive and offset-aware".
    """
    if wert is not None and wert.tzinfo is None:
        return wert.replace(tzinfo=dt.timezone.utc)
    return wert


async def _sichtbare_projekte(db: AsyncSession, user: User) -> dict[int, Project]:
    """Projekte, die dieser Nutzer sehen darf — einmal ermittelt, danach nur noch gelesen."""
    alle = (await db.execute(select(Project))).scalars().all()
    out: dict[int, Project] = {}
    for p in alle:
        try:
            await build_access(p, user, db)
        except Exception:  # noqa: BLE001 — 403/404 bedeutet schlicht: nicht sichtbar
            continue
        out[p.id] = p
    return out


# ── Standard-Satz und Abweichungen ───────────────────────────────────────────

class AbweichungOut(BaseModel):
    project_id: int
    project_key: str
    project_name: str
    definition_id: int
    published: bool


class SlotUebersichtOut(BaseModel):
    slot: str
    name: str
    description: str
    subject_kind: str
    definition_id: int | None = None
    definition_name: str | None = None
    version: int | None = None
    published: bool = False
    updated_at: dt.datetime | None = None
    # Projekte mit eigener Kopie dieses Slots — die folgen dem Satz nicht mehr.
    abweichungen: list[AbweichungOut] = []


@router.get("/processes/slots", response_model=list[SlotUebersichtOut])
async def slot_uebersicht(
    set_id: int | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Belegung eines Satzes samt der Projekte, die davon abweichen.

    Ohne `set_id` der globale Standard — das ist die Grundlage aller Projekte und war
    bisher nur über die API erreichbar.
    """
    if set_id is None:
        s = (await db.execute(select(WorkflowSet).where(
            WorkflowSet.key == sets.BUILTIN_SET_KEY))).scalars().first()
        if s is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Kein globaler Standard-Satz")
    else:
        s = await db.get(WorkflowSet, set_id)
        if s is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Prozess-Satz nicht gefunden")

    sichtbar = await _sichtbare_projekte(db, user)
    # Projekt-eigene Kopien je Slot (nicht archiviert) — das sind genau die Abweichungen.
    kopien = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.slot.isnot(None),
        WorkflowDefinition.project_id.isnot(None),
        WorkflowDefinition.archived_at.is_(None),
    ))).scalars().all()

    out: list[SlotUebersichtOut] = []
    for slot, meta in sets.SLOT_META.items():
        d = await sets.set_definition(db, s.id, slot)
        version = await db.get(WorkflowVersion, d.current_version_id) if d and d.current_version_id else None
        abw = [
            AbweichungOut(
                project_id=k.project_id, project_key=sichtbar[k.project_id].key,
                project_name=sichtbar[k.project_id].name, definition_id=k.id,
                published=bool(k.current_version_id),
            )
            for k in kopien if k.slot == slot and k.project_id in sichtbar
        ]
        out.append(SlotUebersichtOut(
            slot=slot, name=meta["name"], description=meta["description"],
            subject_kind=meta["subject_kind"],
            definition_id=d.id if d else None, definition_name=d.name if d else None,
            version=version.version if version else None,
            published=bool(version), updated_at=d.updated_at if d else None,
            abweichungen=sorted(abw, key=lambda a: a.project_key),
        ))
    return out


# ── Betrieb: was läuft, was hängt ────────────────────────────────────────────

# Ab wann gilt ein wartender Schritt als „hängt"? Wartet ein Ablauf auf einen Menschen,
# ist das normal — nach einem Tag ohne Regung will man es trotzdem sehen.
HAENGT_AB_STUNDEN = 24


class LaufOut(BaseModel):
    id: int
    definition_id: int
    definition_name: str
    slot: str | None = None
    project_id: int | None = None
    project_key: str | None = None
    subject_kind: WorkflowSubjectKind
    # Woran der Ablauf hängt: Ticket-Schlüssel bzw. Exemplar-Kennung.
    subject_ref: str | None = None
    status: WorkflowInstanceStatus
    # Aktueller Halt: Knoten-Beschriftung und worauf gewartet wird.
    node_label: str | None = None
    waiting_for: str | None = None
    seit: dt.datetime | None = None
    stunden: float | None = None
    haengt: bool = False
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
    """Alle Abläufe quer über die Projekte — mit dem Punkt, an dem sie gerade stehen.

    `only_stuck=true` zeigt nur, was Aufmerksamkeit braucht: gescheiterte Instanzen und
    solche, die länger als einen Tag am selben Schritt warten.
    """
    q = select(WorkflowInstance)
    if not include_done:
        # Offen ist alles, was nicht abgeschlossen oder abgebrochen ist — `waiting` gehört
        # ausdrücklich dazu: ein Ablauf, der auf einen Menschen wartet, ist der Normalfall
        # und genau das, was eine Betriebssicht zeigen muss.
        q = q.where(WorkflowInstance.status.notin_(
            [WorkflowInstanceStatus.completed, WorkflowInstanceStatus.cancelled]))
    rows = (await db.execute(q.order_by(WorkflowInstance.id.desc()).limit(limit))).scalars().all()

    sichtbar = await _sichtbare_projekte(db, user)
    jetzt = _now()
    out: list[LaufOut] = []
    for inst in rows:
        if inst.project_id is not None and inst.project_id not in sichtbar:
            continue
        d = await db.get(WorkflowDefinition, inst.definition_id)
        version = await db.get(WorkflowVersion, inst.version_id)
        graph = (version.graph if version else None) or {}

        token = (await db.execute(select(WorkflowToken).where(
            WorkflowToken.instance_id == inst.id,
            WorkflowToken.state.in_([WorkflowTokenState.waiting, WorkflowTokenState.active]),
        ).order_by(WorkflowToken.id.desc()))).scalars().first()
        # Wie lange steht es schon? Der letzte betretene Schritt ist ehrlicher als der
        # Token, dessen Zeitstempel auch ein Weiterschalten im selben Knoten anfasst.
        schritt = (await db.execute(select(WorkflowStepRun).where(
            WorkflowStepRun.instance_id == inst.id,
        ).order_by(WorkflowStepRun.id.desc()))).scalars().first()
        seit = _aware((schritt.entered_at if schritt else None) or inst.started_at)
        stunden = round((jetzt - seit).total_seconds() / 3600, 1) if seit else None
        haengt = (inst.status == WorkflowInstanceStatus.failed
                  or bool(stunden and stunden >= HAENGT_AB_STUNDEN))
        if only_stuck and not haengt:
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
            project_key=sichtbar[inst.project_id].key if inst.project_id in sichtbar else None,
            subject_kind=inst.subject_kind, subject_ref=ref, status=inst.status,
            node_label=_label(graph, token.node_id if token else (schritt.node_id if schritt else None)),
            waiting_for=token.waiting_for if token else None,
            seit=seit, stunden=stunden, haengt=haengt,
            error=inst.error, started_at=inst.started_at,
        ))
    return out


# ── Auslöser: was startet welchen Ablauf ─────────────────────────────────────

class AusloeserOut(BaseModel):
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
    # Nur bei Ereignis-Triggern: Einschränkung auf ein Projekt.
    only_project_id: int | None = None
    enabled: bool = True


@router.get("/processes/triggers", response_model=list[AusloeserOut])
async def ausloeser(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Was einen Ablauf startet — Ereignis, Webhook, Job oder ein anderer Ablauf.

    Gelesen wird aus den veröffentlichten Graphen (Start-Knoten) und den Verweisen in
    Webhooks/Jobs. Eine eigene Trigger-Tabelle gibt es bewusst nicht: der Graph ist die
    Wahrheit und kann so mit keinem Index auseinanderlaufen.
    """
    from ..models.ops import Job, WebhookSub

    sichtbar = await _sichtbare_projekte(db, user)
    ereignis_label = dict(ev.BUILTIN_EVENTS)

    defs = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.archived_at.is_(None)))).scalars().all()
    bekannt = {d.id: d for d in defs
               if d.project_id is None or d.project_id in sichtbar}

    def kopf(d: WorkflowDefinition) -> dict:
        return {
            "definition_id": d.id, "definition_name": d.name, "slot": d.slot,
            "project_id": d.project_id,
            "project_key": sichtbar[d.project_id].key if d.project_id in sichtbar else None,
        }

    out: list[AusloeserOut] = []

    # 1) Ereignis-Trigger am Start-Knoten der veröffentlichten Fassung.
    for d in bekannt.values():
        if not d.current_version_id:
            continue
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = ev.trigger_of(version.graph if version else {})
        if not t or not t.get("event"):
            continue
        name = str(t["event"])
        out.append(AusloeserOut(
            **kopf(d), kind="event", source=name,
            label=ereignis_label.get(name, name),
            only_project_id=t.get("project_id") or None,
            enabled=bool(d.enabled),
        ))

    # 2) Webhooks und Jobs, die unmittelbar auf eine Definition zeigen.
    for hook in (await db.execute(select(WebhookSub).where(
            WebhookSub.workflow_definition_id.isnot(None)))).scalars().all():
        d = bekannt.get(hook.workflow_definition_id)
        if d is None:
            continue
        out.append(AusloeserOut(**kopf(d), kind="webhook", source=hook.route,
                                label=f"Webhook /{hook.route}"))

    for job in (await db.execute(select(Job).where(
            Job.workflow_definition_id.isnot(None)))).scalars().all():
        d = bekannt.get(job.workflow_definition_id)
        if d is None:
            continue
        out.append(AusloeserOut(**kopf(d), kind="job", source=job.name,
                                label=f"Job „{job.name}“", enabled=bool(job.enabled)))

    # 3) Aufrufe aus anderen Abläufen (subflow-Knoten) — sonst wirkte ein Ablauf
    #    auslöserlos, obwohl ihn ein anderer aufruft.
    for d in bekannt.values():
        if not d.current_version_id:
            continue
        version = await db.get(WorkflowVersion, d.current_version_id)
        for n in ((version.graph if version else {}) or {}).get("nodes") or []:
            if n.get("type") != "subflow":
                continue
            slot = ((n.get("data") or {}).get("config") or {}).get("slot")
            if not slot:
                continue
            ziel = next((z for z in bekannt.values() if z.slot == slot), None)
            if ziel is None:
                continue
            out.append(AusloeserOut(**kopf(ziel), kind="subflow", source=d.name,
                                    label=f"Aufruf aus „{d.name}“"))

    # 4) Alles ohne Auslöser: läuft nur, wenn ein Mensch (oder Code) es startet.
    mit_ausloeser = {a.definition_id for a in out}
    for d in bekannt.values():
        if d.id in mit_ausloeser or not d.current_version_id:
            continue
        out.append(AusloeserOut(**kopf(d), kind="manual", source="",
                                label="Nur manuell bzw. aus dem Programm",
                                enabled=bool(d.enabled)))

    return sorted(out, key=lambda a: (a.kind != "event", a.definition_name, a.label))


# ── Ereignis-Katalog (für die Auswahl im Editor und die Übersicht) ───────────

class EreignisOut(BaseModel):
    event: str
    label: str
    listeners: int


@router.get("/processes/events", response_model=list[EreignisOut])
async def ereignisse(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Alle bekannten Ereignisse mit der Zahl der Abläufe, die darauf hören."""
    defs = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.archived_at.is_(None),
        WorkflowDefinition.current_version_id.isnot(None),
    ))).scalars().all()
    zaehler: dict[str, int] = {}
    for d in defs:
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = ev.trigger_of(version.graph if version else {})
        if t and t.get("event"):
            zaehler[str(t["event"])] = zaehler.get(str(t["event"]), 0) + 1
    return [EreignisOut(event=e, label=l, listeners=zaehler.get(e, 0))
            for e, l in ev.BUILTIN_EVENTS]
