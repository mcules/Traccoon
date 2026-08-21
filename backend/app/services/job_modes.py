"""Convert jobs of the old kinds to flows, once.

A job could be five things: ask an agent (`prompt`), start a script (`script`), call a
destination (`http`), kick off a flow (`workflow`) or build the end-of-day film (`film`).
Four of them were the same matter in four executions — with four ways for retry, error and
notification, and all four could do exactly one thing. "First ask, then check, then report"
worked in none of them.

Two kinds remain: `workflow` (schedule plus flow) and `film`. The film stays a kind of its
own because it does nothing but itself — prising it out of its 500 lines would bring no gain
for a single job.

The conversion loses nothing: the prompt becomes the assignment of the agent node, the
parameter set stays context, `notify_mode` becomes a decision in front of the report node,
and `result_html` stays the digest link, because the run number is in the context.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from ..models.ops import Job
from ..models.workflow import WorkflowDefinition, WorkflowVersion

log = logging.getLogger("traccoon.jobs")

OLD_KINDS = ("prompt", "script", "http", "")

# What every recurring flow is given without it standing in the parameter set
# (`scheduler._start_workflow_job` legt es in den Startkontext).
ZEITWERTE = ("today", "now", "since", "window")


def _task(job: Job) -> str:
    """The prompt the way the flow language understands it.

    Both know `{{name}}`, but the job world inserted a list as an enumeration and the flow
    language as what it is — out of eight sources `['Hacker News', …]` would otherwise land in
    the middle of the assignment. The filter says the same thing, only explicitly.
    """
    import re

    from .job_params import parameter

    values = parameter(job.args)
    text = job.prompt or ""
    for name, value in values.items():
        if isinstance(value, (list, tuple)):
            text = re.sub(r"\{\{\s*" + re.escape(name) + r"\s*\}\}",
                          "{{ " + name + ' | join:", " }}', text)
    # A placeholder without a value stayed put literally in the job world — visibly wrong
    # instead of silently empty. The flow language fills it with nothing, so it is said here:
    # whoever needs it enters the value into the start context.
    open_ones = sorted({m for m in re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", text)}
                   - set(values) - set(ZEITWERTE))
    if open_ones:
        log.warning("Job %s: %s ohne Wert im Parametersatz — im Ablauf bleiben sie leer",
                    job.name, ", ".join(open_ones))
    return text

_COL, _ROW = 260, 130


def _n(node_id: str, ntype: str, line: int, config: dict, column: int = 0) -> dict:
    return {"id": node_id, "type": ntype,
            "position": {"x": column * _COL, "y": line * _ROW},
            "data": {"config": config}}


def _e(source: str, target: str, handle: str | None = None, label: str = "") -> dict:
    edge = {"id": f"e-{source}-{handle or 'out'}-{target}", "source": source, "target": target}
    if handle:
        edge["sourceHandle"] = handle
    if label:
        edge["label"] = label
    return edge


def _action(_name: str, _label: str, **params) -> dict:
    """Leading underscore so an action parameter may be called `name` (the store has one)."""
    return {"label": _label, "action": {"action": _name, "params": params}}


def _workstep(job: Job, target_name: str = "") -> tuple[dict, str, dict]:
    """The step that does the actual work.

    Back come the node, the expression for its result and the condition by which a failure is
    recognised — every kind reports it differently (`status`, `ok`).
    """
    if job.kind == "script":
        return (_n("arbeit", "auto_action", 1, _action(
            "script", "Run the script", command=job.command or "",
            args=job.args if isinstance(job.args, list) else [],
            timeout_sec=int(job.run_timeout or 600), context_key="result")),
            "{{ result.output }}", {"==": [{"var": "result.ok"}, False]})
    if job.kind == "http":
        call = dict(job.http_request or {})
        return (_n("arbeit", "auto_action", 1, _action(
            "http_request", "Ziel aufrufen", destination=target_name,
            method=call.get("method") or "GET", path=call.get("path") or "",
            query=call.get("query") or {}, headers=call.get("headers") or {},
            body=call.get("body"), context_key="result")),
            "{{ result.response | json }}", {"==": [{"var": "result.ok"}, False]})
    # prompt (and the empty legacy form)
    return (_n("arbeit", "auto_action", 1, _action(
        "agent_run", "Agenten arbeiten lassen", agent=job.agent or "assistent",
        task=_task(job), title=job.name,
        timeout_sec=int(job.run_timeout or 600), context_key="result")),
        "{{ result.output }}", {"==": [{"var": "result.status"}, "failed"]})


def _key(name: str) -> str:
    """A store key out of the job name: `KI- & Tech-News` → `ki-tech-news`."""
    from ..core.slug import slug

    return slug(name) or "ablage"


def _graph(job: Job, target_name: str = "") -> dict:
    work, result_text, error_condition = _workstep(job, target_name)
    nodes = [
        _n("start", "start", 0, {"label": job.name, "trigger": {"kind": "job"}}),
        work,
        # The answer is the result of the job: the run carries it back into its history, just
        # as a waiting webhook returns it to its caller.
        _n("answer", "auto_action", 2, _action(
            "answer", "Ergebnis festhalten", text=result_text)),
    ]
    edges = [_e("start", "arbeit"), _e("arbeit", "answer")]

    # `result_html` meant: "do not send the text, send the link to the page". The page never
    # existed — the link pointed at `/digest/<run number>`, and behind it lay nothing. A long
    # text does not belong in a message field either: it is put down in a store (like a
    # measurement in its series), and what is reported is the reference to it. That stands
    # before the reporting question, because a silent job should keep what it worked out too.
    if job.result_html:
        nodes.insert(2, _n("ablegen", "auto_action", 2, _action(
            "document", "In die Ablage legen", storage=_key(job.name), name=job.name,
            text=result_text, format="markdown"), column=1))
        edges = [_e("start", "arbeit"), _e("arbeit", "ablegen"), _e("ablegen", "answer")]

    report = job.notify_mode or "always"
    if report == "never":
        nodes.append(_n("fertig", "end", 3, {"label": "Done", "outcome": "completed"}))
        edges.append(_e("answer", "fertig"))
        return {"nodes": nodes, "edges": edges}

    text = "{{ document.title }}\n{{ document.url }}" if job.result_html else result_text
    report_node = _n("melden", "auto_action", 4, _action(
        "notify", "Bescheid geben", kind="job", title=f"Job: {job.name}", text=text), column=-1)

    if report == "always":
        nodes += [report_node, _n("fertig", "end", 5, {"label": "Gemeldet", "outcome": "completed"})]
        edges += [_e("answer", "melden"), _e("melden", "fertig")]
        return {"nodes": nodes, "edges": edges}

    # on_output / on_error: erst hinsehen, dann melden.
    if report == "on_error":
        condition, label = error_condition, "fehlgeschlagen"
    else:
        condition, label = {"!=": [{"var": "answer"}, ""]}, "hat etwas gesagt"
    nodes += [
        _n("melden_wenn", "decision", 3, {
            "label": "Melden?",
            "branches": [{"handle": "melden", "label": label, "guard": condition},
                         {"handle": "still", "label": "still bleiben"}],
            "default_handle": "still"}),
        report_node,
        _n("fertig", "end", 5, {"label": "Done", "outcome": "completed"}),
    ]
    edges += [_e("answer", "melden_wenn"), _e("melden_wenn", "melden", "melden"),
              _e("melden_wenn", "fertig", "still", "ohne Nachricht"), _e("melden", "fertig")]
    from .workflow_terms import stamp
    return stamp({"nodes": nodes, "edges": edges})


async def as_flow(db: AsyncSession, job: Job) -> None:
    """Convert this one job to a flow (without a commit).

    Used on creation as well: whoever enters a job through the API, the agent tool or a
    template no longer gets an old path opened that a later restart would have to collect.
    """
    import datetime as dt

    target_name = ""
    if job.kind == "http" and job.destination_id:
        # The call in the flow names the name, not the number — destinations are resolved by
        # name (project, then user, then system-wide).
        from ..models.destination import Destination
        target = await db.get(Destination, job.destination_id)
        target_name = target.name if target else ""
    # Name and key describe the matter, not the trigger: the job is already called what what
    # it does is meant to be — "KI- & Tech-News", not "Job: 3".
    from .workflow_templates import free_key
    d = WorkflowDefinition(
        project_id=job.project_id,
        key=await free_key(db, job.name, job.project_id), name=job.name,
        description=f"Aus der Job-Art „{job.kind or 'prompt'}“ umgestellt.",
        subject_kind=WorkflowSubjectKind.standalone, enabled=True, created_by=job.user_id)
    db.add(d)
    await db.flush()
    version = WorkflowVersion(
        definition_id=d.id, version=1, graph=_graph(job, target_name), created_by=job.user_id,
        status=WorkflowVersionStatus.published,
        published_at=dt.datetime.now(tz=dt.timezone.utc),
        notes="the job kinds were switched over to flows")
    db.add(version)
    await db.flush()
    d.current_version_id = version.id
    log.info("job %s (%s) now runs through the flow %s", job.name, job.kind or "prompt", d.key)
    job.kind = "workflow"
    job.workflow_definition_id = d.id


async def convert(db: AsyncSession) -> int:
    """Converts every job that still carries an old kind. Returns the count."""
    jobs = (await db.execute(select(Job).where(Job.kind.in_(OLD_KINDS)))).scalars().all()
    for job in jobs:
        await as_flow(db, job)
    if jobs:
        await db.commit()
    return len(jobs)
