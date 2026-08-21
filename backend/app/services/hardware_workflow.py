"""Hardware procurement on the generic workflow engine.

Maps the existing linear procurement process (`hardware_workflow_steps`, by default order →
receive → store → install) as a generic WorkflowDefinition: one human_task per step,
followed by a set_status auto action (when the step name is assigned to a PurchaseStatus).
The workflow is thereby the source of the `purchase_status`; the old module
(`hardware_asset_steps` plus AssetProcurement) keeps running in parallel for the time being
(dual run).
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
from .workflow_terms import migrate_graph

HARDWARE_DEF_KEY = "hardware-beschaffung"
HARDWARE_SLOT = WorkflowSlot.hardware_procurement.value
DEFAULT_STEPS = ["Bestellen", "Erhalten", "Einlagern", "Einbauen"]

# Step name (lower case) to purchase_status. Unknown names produce no status action.
STEP_STATUS_MAP = {
    "bestellen": "ordered",
    "erhalten": "delivered",
    "einlagern": "stored",
    "einbauen": "installed",
}

# Label of the status step: "→ ordered" says nothing to anybody.
STATUS_LABEL = {
    "ordered": "Status: Bestellt",
    "delivered": "Status: Erhalten",
    "stored": "Status: Eingelagert",
    "installed": "Status: Eingebaut",
}


def build_hardware_graph(steps: list[str] | list[tuple[str, dict]]) -> dict:
    """Linearer Graph: start → (human_task[ → auto_action set_status])* → end.

    `steps` are either plain names or (name, AssigneeSpec) pairs. Without a spec the step
    stays unassigned and the handover in the process sets the responsible people.
    """
    pairs: list[tuple[str, dict]] = [
        (s, {}) if isinstance(s, str) else (s[0], s[1] or {}) for s in steps
    ]
    # The flow runs from top to bottom (editor and runtime render vertically).
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
                "assignee": assignee or {"mode": "user"},  # empty = the handover sets the responsible people
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
                # The nested form, the same one the editor writes. In the flat form
                # ("action": "name", "status": …) the interface shows neither the action nor
                # the parameters, and the first edit would overwrite them.
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
    """Steps (name plus AssigneeSpec) of the project procurement workflow, otherwise the default set."""
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
    """Creates (idempotently) the published "hardware procurement" definition of the project.

    If it already exists it is returned unchanged (dual run: existing instances pin to their
    version anyway)."""
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
        description="Built out of the step list of the project — it overrides the set.",
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
    """Step list changed means a new published version of the procurement definition.

    Does nothing when no definition exists for the project yet (then it is only created on
    "edit as a process") or when the graph is unchanged. Running instances stay pinned to
    their old version; only new ones start with the new one.
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
    """Starts (idempotently) a procurement instance for a unit. Precondition: the unit is
    assigned to a project (stock without a project has no workflow)."""
    if asset.project_id is None:
        raise ValueError("A unit without a project has no procurement workflow")
    # Already a running or waiting instance for this unit? Then return that one.
    running = (await db.execute(
        select(WorkflowInstance).where(
            WorkflowInstance.hardware_asset_id == asset.id,
            WorkflowInstance.status.in_(
                [WorkflowInstanceStatus.running, WorkflowInstanceStatus.waiting])
        ).order_by(WorkflowInstance.id.desc())
    )).scalars().first()
    if running is not None:
        return running

    # Which procurement flow applies: the project's own step list, then the set of the
    # project or owner, then the global default.
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


_OLD_ACTION = {"set_purchase_status": "set_status"}


def _comparisonform(graph: dict) -> dict:
    """Reduce the graph to what makes it up in substance, regardless of notation.

    Actions existed in a flat (`{"action": "name", "status": …}`) and a nested form, and the
    status action used to be called differently. Whoever compares only the notation considers
    two identical chains different. Everything else (order, labels, responsible people, form
    fields) stays in: that is how a real adjustment is recognised.
    """
    def action(cfg: dict) -> dict:
        raw = cfg.get("action")
        if isinstance(raw, str):
            # Flat form: everything except name and label is a parameter.
            name = raw
            params = {k: v for k, v in cfg.items()
                      if k not in ("action", "kind", "label", "group")}
            remainder = {k: v for k, v in cfg.items() if k in ("label", "group")}
        elif isinstance(raw, dict):
            name, params = raw.get("action", ""), dict(raw.get("params") or {})
            remainder = {k: v for k, v in cfg.items() if k not in ("action", "kind")}
        else:
            return cfg
        return {**remainder, "action": {"action": _OLD_ACTION.get(name, name), "params": params}}

    nodes = []
    for n in graph.get("nodes") or []:
        cfg = (n.get("data") or {}).get("config") or {}
        nodes.append({"id": n.get("id"), "type": n.get("type"),
                      "position": n.get("position"),
                      "config": action(cfg) if n.get("type") == "auto_action" else cfg})
    edges = [{k: e.get(k) for k in ("id", "source", "target", "sourceHandle")}
             for e in graph.get("edges") or []]
    return {"nodes": nodes, "edges": edges}


async def refresh_generated_definitions(db) -> int:
    """Lift untouched project procurements to the current shape.

    What is touched is only what differs from the freshly produced graph exclusively in
    notation (the earlier flat action form, the old action name). As soon as somebody has
    changed something in substance, the chain stays as it is: an adjustment must never be
    lost silently. Running instances stay on their version anyway.
    """
    rows = (await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.key == HARDWARE_DEF_KEY,
            WorkflowDefinition.project_id.isnot(None),
            WorkflowDefinition.archived_at.is_(None),
        ))).scalars().all()
    renewed = 0
    for d in rows:
        current = (await db.get(WorkflowVersion, d.current_version_id)
                   if d.current_version_id else None)
        if current is None:
            continue
        # With the mark of the terms pass, exactly as in `workflow_seed`: without it the
        # comparison below never holds and every start hangs a fresh, identical version on
        # the chain.
        new, _ = migrate_graph(build_hardware_graph(await _project_steps(db, d.project_id)))
        if new == current.graph:
            continue
        if _comparisonform(current.graph or {}) != _comparisonform(new):
            continue  # adjusted in substance: do not touch
        last = (await db.execute(
            select(WorkflowVersion).where(WorkflowVersion.definition_id == d.id)
            .order_by(WorkflowVersion.version.desc()))).scalars().first()
        version = WorkflowVersion(
            definition_id=d.id, version=(last.version + 1) if last else 1, graph=new,
            status=WorkflowVersionStatus.published,
            published_at=dt.datetime.now(tz=dt.timezone.utc),
            notes="Auf die aktuelle Bauform gehoben (unangetastete Kette)",
        )
        db.add(version)
        await db.flush()
        d.current_version_id = version.id
        renewed += 1
    if renewed:
        await db.commit()
    return renewed
