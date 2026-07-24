"""Etappe 4 — Hardware-Beschaffung auf der generischen Workflow-Engine.

Bildet den bestehenden linearen Beschaffungsprozess (`hardware_workflow_steps`,
Default Bestellen→Erhalten→Einlagern→Einbauen) als generische WorkflowDefinition ab:
je Schritt ein human_task, gefolgt von einer set_purchase_status-Auto-Action (falls der
Schrittname einem PurchaseStatus zugeordnet ist). Der Workflow ist damit die Quelle des
`purchase_status`; das alte Modul (`hardware_asset_steps` + AssetProcurement) läuft
zunächst parallel weiter (Dual-Run).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from ..models.hardware import HardwareAsset, HardwareModel, HardwareWorkflow, HardwareWorkflowStep
from ..models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from .workflow_engine import start_workflow

HARDWARE_DEF_KEY = "hardware-beschaffung"
DEFAULT_STEPS = ["Bestellen", "Erhalten", "Einlagern", "Einbauen"]

# Schrittname (kleingeschrieben) → purchase_status. Unbekannte Namen erzeugen keine Status-Aktion.
STEP_STATUS_MAP = {
    "bestellen": "ordered",
    "erhalten": "delivered",
    "einlagern": "stored",
    "einbauen": "installed",
}


def build_hardware_graph(steps: list[str]) -> dict:
    """Linearer Graph: start → (human_task[ → auto_action set_purchase_status])* → end."""
    nodes: list[dict] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
    ]
    edges: list[dict] = []
    prev = "s"
    x = 200
    for i, name in enumerate(steps):
        ht = f"ht{i}"
        nodes.append({
            "id": ht, "type": "human_task", "position": {"x": x, "y": 0},
            "data": {"config": {
                "label": name,
                "assignee": {"mode": "user"},   # unassigned; Übergabe setzt Zuständige
                "form": [{"key": "note", "label": "Notiz", "type": "text"}],
                "handover": True,
            }},
        })
        edges.append({"id": f"e-{prev}-{ht}", "source": prev, "target": ht})
        prev = ht
        x += 200
        status = STEP_STATUS_MAP.get(name.strip().lower())
        if status:
            aa = f"aa{i}"
            nodes.append({
                "id": aa, "type": "auto_action", "position": {"x": x, "y": 0},
                "data": {"config": {
                    "label": f"→ {status}",
                    "action": "set_purchase_status", "status": status,
                }},
            })
            edges.append({"id": f"e-{prev}-{aa}", "source": prev, "target": aa})
            prev = aa
            x += 200
    nodes.append({"id": "e", "type": "end", "position": {"x": x, "y": 0},
                  "data": {"config": {"outcome": "completed"}}})
    edges.append({"id": f"e-{prev}-e", "source": prev, "target": "e"})
    return {"nodes": nodes, "edges": edges}


async def _project_step_names(db, project_id: int) -> list[str]:
    """Schrittnamen des Projekt-Beschaffungs-Workflows (oder Default-Satz)."""
    wf = (await db.execute(
        select(HardwareWorkflow).where(HardwareWorkflow.project_id == project_id)
    )).scalar_one_or_none()
    if wf is not None:
        rows = (await db.execute(
            select(HardwareWorkflowStep).where(HardwareWorkflowStep.workflow_id == wf.id)
            .order_by(HardwareWorkflowStep.order)
        )).scalars().all()
        if rows:
            return [s.name for s in rows]
    return list(DEFAULT_STEPS)


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

    steps = await _project_step_names(db, project_id)
    graph = build_hardware_graph(steps)
    definition = WorkflowDefinition(
        project_id=project_id, key=HARDWARE_DEF_KEY, name="Hardware-Beschaffung",
        description="Automatisch aus dem Beschaffungs-Workflow erzeugt (Etappe 4).",
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
