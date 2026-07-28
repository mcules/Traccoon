"""Etappe 4 — Hardware-Beschaffung auf der generischen Workflow-Engine.

Bildet den bestehenden linearen Beschaffungsprozess (`hardware_workflow_steps`,
Default Bestellen→Erhalten→Einlagern→Einbauen) als generische WorkflowDefinition ab:
je Schritt ein human_task, gefolgt von einer set_status-Auto-Action (falls der
Schrittname einem PurchaseStatus zugeordnet ist). Der Workflow ist damit die Quelle des
`purchase_status`; das alte Modul (`hardware_asset_steps` + AssetProcurement) läuft
zunächst parallel weiter (Dual-Run).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..models.enums import (
    WorkflowInstanceStatus, WorkflowSlot, WorkflowSubjectKind, WorkflowVersionStatus,
)
from ..models.hardware import HardwareAsset, HardwareModel, HardwareWorkflow, HardwareWorkflowStep
from ..models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from .workflow_engine import start_workflow

HARDWARE_DEF_KEY = "hardware-beschaffung"
HARDWARE_SLOT = WorkflowSlot.hardware_procurement.value
DEFAULT_STEPS = ["Bestellen", "Erhalten", "Einlagern", "Einbauen"]

# Schrittname (kleingeschrieben) → purchase_status. Unbekannte Namen erzeugen keine Status-Aktion.
STEP_STATUS_MAP = {
    "bestellen": "ordered",
    "erhalten": "delivered",
    "einlagern": "stored",
    "einbauen": "installed",
}

# Beschriftung des Status-Schritts — „→ ordered" sagt niemandem etwas.
STATUS_LABEL = {
    "ordered": "Status: Bestellt",
    "delivered": "Status: Erhalten",
    "stored": "Status: Eingelagert",
    "installed": "Status: Eingebaut",
}


def build_hardware_graph(steps: list[str] | list[tuple[str, dict]]) -> dict:
    """Linearer Graph: start → (human_task[ → auto_action set_status])* → end.

    `steps` sind entweder reine Namen oder (Name, AssigneeSpec)-Paare. Ohne Spec bleibt
    der Schritt unzugewiesen und die Übergabe im Prozess setzt die Zuständigen.
    """
    pairs: list[tuple[str, dict]] = [
        (s, {}) if isinstance(s, str) else (s[0], s[1] or {}) for s in steps
    ]
    # Fluss läuft von oben nach unten (Editor/Runtime rendern vertikal).
    nodes: list[dict] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
    ]
    edges: list[dict] = []
    prev = "s"
    y = 140
    for i, (name, assignee) in enumerate(pairs):
        ht = f"ht{i}"
        nodes.append({
            "id": ht, "type": "human_task", "position": {"x": 0, "y": y},
            "data": {"config": {
                "label": name,
                "group": "beschaffung",
                "assignee": assignee or {"mode": "user"},  # leer = Übergabe setzt Zuständige
                "form": [{"key": "note", "label": "Notiz", "type": "text"}],
                "handover": True,
            }},
        })
        edges.append({"id": f"e-{prev}-{ht}", "source": prev, "target": ht})
        prev = ht
        y += 140
        status = STEP_STATUS_MAP.get(name.strip().lower())
        if status:
            aa = f"aa{i}"
            nodes.append({
                "id": aa, "type": "auto_action", "position": {"x": 0, "y": y},
                # Verschachtelte Form — dieselbe, die der Editor schreibt. In der flachen
                # Form („action": "name", "status": …) zeigt die Oberfläche weder Aktion
                # noch Parameter an, und die erste Bearbeitung würde sie überschreiben.
                "data": {"config": {
                    "label": STATUS_LABEL.get(status, f"Status: {status}"),
                    "group": "beschaffung",
                    "action": {"action": "set_status", "params": {"status": status}},
                }},
            })
            edges.append({"id": f"e-{prev}-{aa}", "source": prev, "target": aa})
            prev = aa
            y += 140
    nodes.append({"id": "e", "type": "end", "position": {"x": 0, "y": y},
                  "data": {"config": {"outcome": "completed"}}})
    edges.append({"id": f"e-{prev}-e", "source": prev, "target": "e"})
    return {"nodes": nodes, "edges": edges}


async def _project_steps(db, project_id: int) -> list[tuple[str, dict]]:
    """Schritte (Name + AssigneeSpec) des Projekt-Beschaffungs-Workflows, sonst Default-Satz."""
    wf = (await db.execute(
        select(HardwareWorkflow).where(HardwareWorkflow.project_id == project_id)
    )).scalar_one_or_none()
    if wf is not None:
        rows = (await db.execute(
            select(HardwareWorkflowStep).where(HardwareWorkflowStep.workflow_id == wf.id)
            .order_by(HardwareWorkflowStep.order)
        )).scalars().all()
        if rows:
            return [(s.name, s.assignee or {}) for s in rows]
    return [(name, {}) for name in DEFAULT_STEPS]


async def ensure_hardware_definition(db, project_id: int, actor_id: int | None = None
                                     ) -> WorkflowDefinition:
    """Legt (idempotent) die veröffentlichte „Hardware-Beschaffung"-Definition des Projekts an.

    Existiert sie bereits, wird sie unverändert zurückgegeben (Dual-Run: bestehende Instanzen
    pinnen ohnehin auf ihre Version)."""
    existing = (await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.project_id == project_id,
            WorkflowDefinition.key == HARDWARE_DEF_KEY)
    )).scalar_one_or_none()
    if existing is not None:
        return existing

    steps = await _project_steps(db, project_id)
    graph = build_hardware_graph(steps)
    definition = WorkflowDefinition(
        project_id=project_id, key=HARDWARE_DEF_KEY, slot=HARDWARE_SLOT,
        name="Hardware-Beschaffung",
        description="Aus der Schrittliste des Projekts erzeugt — überschreibt den Satz.",
        subject_kind=WorkflowSubjectKind.hardware_asset, created_by=actor_id,
    )
    db.add(definition)
    await db.flush()
    version = WorkflowVersion(
        definition_id=definition.id, version=1, graph=graph,
        status=WorkflowVersionStatus.published,
        published_at=dt.datetime.now(tz=dt.timezone.utc), created_by=actor_id,
    )
    db.add(version)
    await db.flush()
    definition.current_version_id = version.id
    await db.commit()
    await db.refresh(definition)
    return definition


async def sync_hardware_definition(db, project_id: int, actor_id: int | None = None
                                   ) -> WorkflowDefinition | None:
    """Schrittliste geändert → neue veröffentlichte Version der Beschaffungs-Definition.

    Tut nichts, wenn für das Projekt noch keine Definition existiert (dann wird sie erst
    beim „Als Prozess bearbeiten" erzeugt) oder wenn der Graph unverändert ist. Laufende
    Instanzen bleiben auf ihrer alten Version gepinnt — nur neue starten mit der neuen.
    """
    definition = (await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.project_id == project_id,
            WorkflowDefinition.key == HARDWARE_DEF_KEY)
    )).scalar_one_or_none()
    if definition is None:
        return None
    graph = build_hardware_graph(await _project_steps(db, project_id))
    current = (await db.get(WorkflowVersion, definition.current_version_id)
               if definition.current_version_id else None)
    if current is not None and current.graph == graph:
        return definition
    last = (await db.execute(
        select(WorkflowVersion).where(WorkflowVersion.definition_id == definition.id)
        .order_by(WorkflowVersion.version.desc())
    )).scalars().first()
    version = WorkflowVersion(
        definition_id=definition.id, version=(last.version + 1) if last else 1, graph=graph,
        status=WorkflowVersionStatus.published,
        published_at=dt.datetime.now(tz=dt.timezone.utc), created_by=actor_id,
    )
    db.add(version)
    await db.flush()
    definition.current_version_id = version.id
    await db.commit()
    await db.refresh(definition)
    return definition


async def start_hardware_instance(db, asset: HardwareAsset, actor_id: int | None = None
                                  ) -> WorkflowInstance:
    """Startet (idempotent) eine Beschaffungs-Instanz für ein Exemplar. Voraussetzung:
    Exemplar ist einem Projekt zugeordnet (Vorrat/Lager ohne Projekt hat keinen Workflow)."""
    if asset.project_id is None:
        raise ValueError("Exemplar ohne Projekt hat keinen Beschaffungs-Workflow")
    # Bereits eine laufende/wartende Instanz für dieses Exemplar? → diese zurückgeben.
    running = (await db.execute(
        select(WorkflowInstance).where(
            WorkflowInstance.hardware_asset_id == asset.id,
            WorkflowInstance.status.in_(
                [WorkflowInstanceStatus.running, WorkflowInstanceStatus.waiting])
        ).order_by(WorkflowInstance.id.desc())
    )).scalars().first()
    if running is not None:
        return running

    # Welcher Beschaffungs-Ablauf gilt: eigene Schrittliste des Projekts → Satz des
    # Projekts/Owners → globaler Standard.
    from .workflow_sets import resolve_definition
    definition = await resolve_definition(db, asset.project_id, HARDWARE_SLOT)
    if definition is None or definition.current_version_id is None:
        definition = await ensure_hardware_definition(db, asset.project_id, actor_id)
    model = await db.get(HardwareModel, asset.model_id)
    context = {
        "asset_id": asset.id, "project_id": asset.project_id,
        "model_name": model.name if model else "", "serial_number": asset.serial_number or "",
    }
    return await start_workflow(
        db, definition, subject_kind=WorkflowSubjectKind.hardware_asset,
        hardware_asset_id=asset.id, context=context, actor_id=actor_id, source="hardware_asset",
    )


_ALT_AKTION = {"set_purchase_status": "set_status"}


def _vergleichsform(graph: dict) -> dict:
    """Graph auf das reduzieren, was ihn inhaltlich ausmacht — Schreibweise egal.

    Aktionen gab es in flacher (`{"action": "name", "status": …}`) und verschachtelter Form,
    und die Zustands-Aktion hieß früher anders. Wer nur die Schreibweise vergleicht, hält
    zwei gleiche Ketten für verschieden. Alles andere — Reihenfolge, Beschriftungen,
    Zuständige, Formularfelder — bleibt drin: daran erkennt man eine echte Anpassung.
    """
    def aktion(cfg: dict) -> dict:
        roh = cfg.get("action")
        if isinstance(roh, str):
            # Flache Form: alles außer Name und Beschriftung ist Parameter.
            name = roh
            params = {k: v for k, v in cfg.items()
                      if k not in ("action", "kind", "label", "group")}
            rest = {k: v for k, v in cfg.items() if k in ("label", "group")}
        elif isinstance(roh, dict):
            name, params = roh.get("action", ""), dict(roh.get("params") or {})
            rest = {k: v for k, v in cfg.items() if k not in ("action", "kind")}
        else:
            return cfg
        return {**rest, "action": {"action": _ALT_AKTION.get(name, name), "params": params}}

    nodes = []
    for n in graph.get("nodes") or []:
        cfg = (n.get("data") or {}).get("config") or {}
        nodes.append({"id": n.get("id"), "type": n.get("type"),
                      "position": n.get("position"),
                      "config": aktion(cfg) if n.get("type") == "auto_action" else cfg})
    edges = [{k: e.get(k) for k in ("id", "source", "target", "sourceHandle")}
             for e in graph.get("edges") or []]
    return {"nodes": nodes, "edges": edges}


async def refresh_generated_definitions(db) -> int:
    """Unangetastete Projekt-Beschaffungen auf die aktuelle Bauform heben.

    Angefasst wird nur, was sich vom frisch erzeugten Graphen ausschließlich in der
    Schreibweise unterscheidet (frühere flache Aktions-Form, alter Aktionsname). Sobald
    jemand inhaltlich etwas geändert hat, bleibt die Kette wie sie ist — eine Anpassung darf
    nie stillschweigend verloren gehen. Laufende Instanzen bleiben ohnehin auf ihrer Version.
    """
    rows = (await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.key == HARDWARE_DEF_KEY,
            WorkflowDefinition.project_id.isnot(None),
            WorkflowDefinition.archived_at.is_(None),
        ))).scalars().all()
    erneuert = 0
    for d in rows:
        current = (await db.get(WorkflowVersion, d.current_version_id)
                   if d.current_version_id else None)
        if current is None:
            continue
        neu = build_hardware_graph(await _project_steps(db, d.project_id))
        if neu == current.graph:
            continue
        if _vergleichsform(current.graph or {}) != _vergleichsform(neu):
            continue  # inhaltlich angepasst — nicht anfassen
        last = (await db.execute(
            select(WorkflowVersion).where(WorkflowVersion.definition_id == d.id)
            .order_by(WorkflowVersion.version.desc()))).scalars().first()
        version = WorkflowVersion(
            definition_id=d.id, version=(last.version + 1) if last else 1, graph=neu,
            status=WorkflowVersionStatus.published,
            published_at=dt.datetime.now(tz=dt.timezone.utc),
            notes="Auf die aktuelle Bauform gehoben (unangetastete Kette)",
        )
        db.add(version)
        await db.flush()
        d.current_version_id = version.id
        erneuert += 1
    if erneuert:
        await db.commit()
    return erneuert
