"""The flow behind the job "Alte Unterhaltungen aufräumen".

Deleting a conversation is deliberately not a button. It is a workflow action
(`assistant_session`, op `delete`), and this is the flow that carries it, so that clearing
out can be scheduled instead of remembered.

Everything the job brings of its own stands in its start context (`jobs.args`, in the UI the
JSON field next to the flow), exactly as with the research flow:

    closed_only       true = only closed conversations (the sensible default)
    older_than_days   how old the last message has to be
    keep_last         never touch the N most recent
    agent             only conversations of this agent (empty = all)

The graph is deliberately small. What protects here are the guard rails inside the
action (nothing running, nothing open, nothing without an owner), not a construction in the
picture — a rail that stands in the graph can be edited away by accident, one that stands in
the action cannot.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from ..models.workflow import WorkflowDefinition, WorkflowVersion
from .workflow_terms import migrate_graph

log = logging.getLogger("traccoon.assistant.cleanup")

KEY = "unterhaltungen-aufraeumen"
NAME = "Alte Unterhaltungen aufräumen"
DESCRIPTION = (
    "Löscht alte Unterhaltungen des persönlichen Assistenten. Alles Job-Eigene steht im "
    "Startkontext:\n"
    "  closed_only      — nur geschlossene (Vorgabe: ja)\n"
    "  older_than_days  — wie alt die letzte Nachricht sein muss\n"
    "  keep_last        — die N jüngsten bleiben in jedem Fall\n"
    "  agent            — nur Unterhaltungen dieses Agenten (leer = alle)\n"
    "Was gerade läuft, wird nie gelöscht; Offenes nur, wenn eine Nummer genannt ist."
)

_COL, _ROW = 280, 130

# Only reported when something really went. A job that says every night that it deleted
# nothing is a job one switches off after a week.
MELDEN_GUARD = {">": [{"var": ["cleanup.deleted", 0]}, 0]}


def _n(node_id: str, ntype: str, col: int, row: int, config: dict) -> dict:
    return {"id": node_id, "type": ntype, "position": {"x": col * _COL, "y": row * _ROW},
            "data": {"config": config}}


def _action(_name: str, _label: str, **params) -> dict:
    return {"label": _label, "action": {"action": _name, "params": params}}


def _e(source: str, target: str, handle: str | None = None, label: str = "") -> dict:
    edge = {"id": f"e-{source}-{handle or 'out'}-{target}", "source": source, "target": target}
    if handle:
        edge["sourceHandle"] = handle
    if label:
        edge["label"] = label
    return edge


def build() -> dict:
    return {
        "nodes": [
            _n("start", "start", 0, 0, {"label": "Aufräum-Job", "trigger": {"kind": "job"}}),
            _n("loeschen", "auto_action", 0, 1, _action(
                "assistant_session", "Alte Unterhaltungen löschen", op="delete",
                closed_only="{{ closed_only }}", older_than_days="{{ older_than_days }}",
                keep_last="{{ keep_last }}", agent="{{ agent }}", context_key="cleanup")),
            _n("melden_wenn", "decision", 0, 2, {
                "label": "Melden?",
                "branches": [
                    {"handle": "melden", "label": "es wurde gelöscht", "guard": MELDEN_GUARD},
                    {"handle": "still", "label": "nichts zu tun"}],
                "default_handle": "still"}),
            _n("melden", "auto_action", -1, 3, _action(
                "notify", "Bescheid geben", kind="job", title="Job: {{ job.name }}",
                text="{{ cleanup.deleted }} alte Unterhaltungen gelöscht.")),
            _n("fertig", "end", 0, 4, {"label": "Fertig", "outcome": "completed"}),
        ],
        "edges": [
            _e("start", "loeschen"),
            _e("loeschen", "melden_wenn"),
            _e("melden_wenn", "melden", "melden"),
            _e("melden", "fertig"),
            _e("melden_wenn", "fertig", "still", "ohne Nachricht"),
        ],
    }


async def find(db: AsyncSession) -> WorkflowDefinition | None:
    return (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.key == KEY, WorkflowDefinition.project_id.is_(None),
        WorkflowDefinition.archived_at.is_(None)))).scalars().first()


async def ensure(db: AsyncSession) -> WorkflowDefinition:
    """Create the flow if it is not there. Never overwrite it.

    Same reasoning as `research_flow.ensure`: this flow is MEANT to be edited (whoever wants
    a different message text should get it), so the code is the seed and not the master. A
    difference is only logged.
    """
    d = await find(db)
    if d is not None:
        current = (await db.get(WorkflowVersion, d.current_version_id)
                   if d.current_version_id else None)
        if current is not None:
            if current.graph != migrate_graph(build())[0]:
                log.info("cleanup flow %s differs from the code — kept as it stands (v%s)",
                         KEY, current.version)
            return d
    graph, _ = migrate_graph(build())
    if d is None:
        d = WorkflowDefinition(
            project_id=None, key=KEY, name=NAME, description=DESCRIPTION,
            subject_kind=WorkflowSubjectKind.standalone, enabled=True)
        db.add(d)
        await db.flush()
    last = (await db.execute(select(WorkflowVersion)
                             .where(WorkflowVersion.definition_id == d.id)
                             .order_by(WorkflowVersion.version.desc()))).scalars().first()
    version = WorkflowVersion(
        definition_id=d.id, version=(last.version + 1) if last else 1, graph=graph,
        status=WorkflowVersionStatus.published,
        published_at=dt.datetime.now(tz=dt.timezone.utc),
        notes="Der Aufräum-Ablauf für alte Unterhaltungen, wie er im Code steht.")
    db.add(version)
    await db.flush()
    d.current_version_id = version.id
    await db.commit()
    log.info("cleanup flow %s created as version %s", KEY, version.version)
    return d
