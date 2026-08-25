"""The one flow behind every recurring research job.

Before this, every research job carried its own flow: the AI and tech digest and the Qwen
watcher differed in their prompt, in whether the result is filed, and in when a message goes
out — and were otherwise the same five nodes twice. Whoever improved one of them improved
one of them.

So: ONE flow, and everything a job brings of its own stands in its start context
(`jobs.args`, in the UI the JSON field next to the flow):

    auftrag     the research assignment as text (required)
    agent       the role with web search (default `news`)
    ablage      key of the store; EMPTY = do not file, the text itself is reported
    still_wenn  word at which the flow stays silent; EMPTY = always report

The time values are appended by the flow itself. They cannot stand in the assignment:
`{{…}}` is replaced exactly ONE round, so a placeholder INSIDE a context value (and the
assignment is one) would stay there literally.

`ensure` runs at backend start, in the same manner as `workflow_seed.ensure_builtin_set`: a
new version is published only when the graph has really changed.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from ..models.workflow import WorkflowDefinition, WorkflowVersion
from .workflow_terms import migrate_graph

log = logging.getLogger("traccoon.research")

KEY = "recherche"
NAME = "Research (generic)"
DESCRIPTION = (
    "One flow for every recurring research job. Everything a job brings of its own stands "
    "in the start context:\n"
    "  auftrag     the research assignment as text (required)\n"
    "  agent       the role with web search (default: news)\n"
    "  ablage      key of the store; empty = do not file, the text itself is reported\n"
    "  still_wenn  word at which the flow stays silent; empty = always report\n"
    "The time values (today, now, window, since) are appended by the flow itself. The context "
    "keys keep their original names: they stand in the parameters of every job that runs on "
    "this flow."
)

_COL, _ROW = 280, 130

# The assignment plus what only the run knows. Deliberately AFTER the assignment: whoever
# reads the prompt sees their own text first.
TASK = """{{ auftrag }}

----
Facts about this run (set by the job, do not invent them and do not overwrite them):
Today: {{ today }} · Now: {{ now }}
Window: {{ window }}
Last successful run: {{ since }}"""

# Report when there IS something — and the job may name a word at which it stays silent.
# Every `var` carries its stand-in: a missing key would otherwise become `None`, and
# `None in "text"` raises a TypeError which the engine does not catch (it catches
# `JsonLogicError`), so the run would break instead of staying silent.
MELDEN_GUARD = {"and": [
    {"!=": [{"var": ["answer", ""]}, ""]},
    {"or": [
        {"==": [{"var": ["still_wenn", ""]}, ""]},
        # A substring, not equality: an agent puts a sentence around its keyword more often
        # than not, and that sentence must not turn silence into a message.
        {"!": {"in": [{"var": ["still_wenn", ""]}, {"var": ["answer", ""]}]}},
    ]},
]}

ABLAGE_GUARD = {"!=": [{"var": ["ablage", ""]}, ""]}


def _n(node_id: str, ntype: str, col: int, row: int, config: dict) -> dict:
    return {"id": node_id, "type": ntype, "position": {"x": col * _COL, "y": row * _ROW},
            "data": {"config": config}}


def _action(_name: str, _label: str, **params) -> dict:
    """Leading underscore so a parameter may be called `name` (the store has one)."""
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
            _n("start", "start", 0, 0, {"label": "Research job", "trigger": {"kind": "job"}}),
            # `timeout_sec` is read as a number, not interpolated — hence firmly in the graph.
            _n("arbeit", "auto_action", 0, 1, _action(
                "agent_run", "Let an agent do the research",
                agent='{{ agent | default:"news" }}', task=TASK,
                title="{{ job.name }}", timeout_sec=900, context_key="result")),
            # Trimmed: without it a lone line break would count as "said something".
            _n("antwort", "auto_action", 0, 2, _action(
                "answer", "Hold on to the result", text="{{ result.output | trim }}")),
            _n("ablegen_wenn", "decision", 0, 3, {
                "label": "File it in a store?",
                "branches": [
                    {"handle": "ablegen", "label": "a store was named", "guard": ABLAGE_GUARD},
                    {"handle": "direkt", "label": "without a store"}],
                "default_handle": "direkt"}),
            _n("ablegen", "auto_action", 1, 4, _action(
                "document", "Put it in the store", storage="{{ ablage }}", name="{{ job.name }}",
                text="{{ answer }}", format="markdown")),
            # A long text does not belong in a message field: what is reported is the
            # reference to it.
            _n("bericht_link", "auto_action", 1, 5, _action(
                "answer", "Report = link to the store", context_key="bericht",
                text="{{ document.title }}\n{{ document.url }}")),
            _n("bericht_text", "auto_action", -1, 4, _action(
                "answer", "Report = the answer itself", context_key="bericht",
                text="{{ answer }}")),
            _n("melden_wenn", "decision", 0, 6, {
                "label": "Report?",
                "branches": [
                    {"handle": "melden", "label": "worth reporting", "guard": MELDEN_GUARD},
                    {"handle": "still", "label": "stay silent"}],
                "default_handle": "still"}),
            _n("melden", "auto_action", -1, 7, _action(
                "notify", "Say something", kind="job", title="Job: {{ job.name }}",
                text="{{ bericht }}")),
            _n("fertig", "end", 0, 8, {"label": "Done", "outcome": "completed"}),
        ],
        "edges": [
            _e("start", "arbeit"),
            _e("arbeit", "antwort"),
            _e("antwort", "ablegen_wenn"),
            _e("ablegen_wenn", "ablegen", "ablegen"),
            _e("ablegen", "bericht_link"),
            _e("bericht_link", "melden_wenn"),
            _e("ablegen_wenn", "bericht_text", "direkt", "without a store"),
            _e("bericht_text", "melden_wenn"),
            _e("melden_wenn", "melden", "melden"),
            _e("melden", "fertig"),
            _e("melden_wenn", "fertig", "still", "without a message"),
        ],
    }


async def find(db: AsyncSession) -> WorkflowDefinition | None:
    """The shared research flow, if it is there. Global, not one of a project."""
    return (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.key == KEY, WorkflowDefinition.project_id.is_(None),
        WorkflowDefinition.archived_at.is_(None)))).scalars().first()


async def ensure(db: AsyncSession) -> WorkflowDefinition:
    """Create the flow if it is not there. Never overwrite it.

    Deliberately not the manner of `workflow_seed`: that one republishes its graphs on every
    change in the code, because nobody edits the shipped set in place — it is copied. This
    flow is the opposite, it is MEANT to be edited: it stands in the editor, it stands in the
    flow picker, and whoever tunes their report text there would find their change gone after
    the next restart. So the code is the seed here, not the master. A difference is only
    logged, so an improvement in the code stays visible without forcing itself.
    """
    d = await find(db)
    if d is not None:
        current = (await db.get(WorkflowVersion, d.current_version_id)
                   if d.current_version_id else None)
        if current is not None:
            if current.graph != migrate_graph(build())[0]:
                log.info("research flow %s differs from the code — kept as it stands (v%s)",
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
        notes="The shared research flow, as it stands in the code.")
    db.add(version)
    await db.flush()
    d.current_version_id = version.id
    await db.commit()
    log.info("research flow %s created as version %s", KEY, version.version)
    return d
