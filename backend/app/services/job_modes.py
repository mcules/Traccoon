"""Jobs der alten Arten einmalig auf Abläufe umstellen.

Ein Job konnte fünferlei sein: einen Agenten fragen (`prompt`), ein Skript starten
(`script`), ein Ziel aufrufen (`http`), einen Ablauf anstoßen (`workflow`) oder den
Feierabendfilm bauen (`film`). Vier davon waren dieselbe Sache in vier Ausführungen — mit
vier Wegen für Wiederholung, Fehler und Benachrichtigung, und alle vier konnten nur genau
eins. „Erst fragen, dann prüfen, dann melden“ ging in keinem davon.

Es bleiben zwei Arten: `workflow` (Zeitplan plus Ablauf) und `film`. Der Film bleibt eine
Art für sich, weil er nichts weiter tut als sich selbst — ihn aus seinen 500 Zeilen zu
lösen, brächte für einen einzigen Job keinen Gewinn.

Die Umstellung verliert nichts: Der Prompt wird zum Auftrag des Agenten-Knotens, der
Parametersatz bleibt Kontext, `notify_mode` wird zu einer Weiche vor dem Melde-Knoten, und
`result_html` bleibt der Digest-Link, weil die Lauf-Nummer im Kontext steht.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from ..models.ops import Job
from ..models.workflow import WorkflowDefinition, WorkflowVersion

log = logging.getLogger("traccoon.jobs")

ALTE_KINDS = ("prompt", "script", "http", "")

# Was jeder wiederkehrende Ablauf mitbekommt, ohne dass es im Parametersatz steht
# (`scheduler._start_workflow_job` legt es in den Startkontext).
ZEITWERTE = ("today", "now", "since", "window")


def _task(job: Job) -> str:
    """Der Prompt, wie ihn die Ablauf-Sprache versteht.

    Beide kennen `{{name}}`, aber eine Liste setzte die Job-Welt als Aufzählung ein und die
    Ablauf-Sprache als das, was sie ist — aus acht Quellen würde sonst `['Hacker News', …]`
    mitten im Auftrag. Der Filter sagt dasselbe, nur ausdrücklich.
    """
    import re

    from .job_params import parameter

    values = parameter(job.args)
    text = job.prompt or ""
    for name, value in values.items():
        if isinstance(value, (list, tuple)):
            text = re.sub(r"\{\{\s*" + re.escape(name) + r"\s*\}\}",
                          "{{ " + name + ' | join:", " }}', text)
    # Ein Platzhalter ohne Wert blieb in der Job-Welt wörtlich stehen — sichtbar falsch statt
    # lautlos leer. Die Ablauf-Sprache füllt ihn mit nichts, also wird er hier gesagt: Wer
    # ihn braucht, trägt den Wert in den Startkontext nach.
    offen = sorted({m for m in re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", text)}
                   - set(values) - set(ZEITWERTE))
    if offen:
        log.warning("Job %s: %s ohne Wert im Parametersatz — im Ablauf bleiben sie leer",
                    job.name, ", ".join(offen))
    return text

_COL, _ROW = 260, 130


def _n(node_id: str, ntype: str, line: int, config: dict, spalte: int = 0) -> dict:
    return {"id": node_id, "type": ntype,
            "position": {"x": spalte * _COL, "y": line * _ROW},
            "data": {"config": config}}


def _e(source: str, target: str, handle: str | None = None, label: str = "") -> dict:
    edge = {"id": f"e-{source}-{handle or 'out'}-{target}", "source": source, "target": target}
    if handle:
        edge["sourceHandle"] = handle
    if label:
        edge["label"] = label
    return edge


def _action(_name: str, _label: str, **params) -> dict:
    """Unterstrich vorn, damit ein Aktionsparameter `name` heißen darf (die Ablage hat einen)."""
    return {"label": _label, "action": {"action": _name, "params": params}}


def _arbeitsschritt(job: Job, target_name: str = "") -> tuple[dict, str, dict]:
    """Der Schritt, der die eigentliche Arbeit macht.

    Zurück kommen der Knoten, der Ausdruck für sein Ergebnis und die Bedingung, an der man
    einen Fehlschlag erkennt — jede Art meldet ihn anders (`status`, `ok`).
    """
    if job.kind == "script":
        return (_n("arbeit", "auto_action", 1, _action(
            "script", "Skript ausführen", command=job.command or "",
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
    # prompt (und die leere Altform)
    return (_n("arbeit", "auto_action", 1, _action(
        "agent_run", "Agenten arbeiten lassen", agent=job.agent or "assistent",
        task=_task(job), title=job.name,
        timeout_sec=int(job.run_timeout or 600), context_key="result")),
        "{{ result.output }}", {"==": [{"var": "result.status"}, "failed"]})


def _key(name: str) -> str:
    """Aus dem Job-Namen ein Ablagen-Schlüssel: `KI- & Tech-News` → `ki-tech-news`."""
    from ..core.slug import slug

    return slug(name) or "ablage"


def _graph(job: Job, target_name: str = "") -> dict:
    arbeit, result_text, error_bedingung = _arbeitsschritt(job, target_name)
    nodes = [
        _n("start", "start", 0, {"label": job.name, "trigger": {"kind": "job"}}),
        arbeit,
        # Die Antwort ist das Ergebnis des Jobs: Der Lauf trägt sie in seine Historie zurück,
        # genau wie ein wartender Webhook sie an seinen Aufrufer zurückgibt.
        _n("answer", "auto_action", 2, _action(
            "answer", "Ergebnis festhalten", text=result_text)),
    ]
    edges = [_e("start", "arbeit"), _e("arbeit", "answer")]

    # `result_html` hieß: „schick nicht den Text, schick den Link auf die Seite". Die Seite
    # gab es nie — der Link zeigte auf `/digest/<Lauf-Nummer>`, und dahinter lag nichts. Ein
    # langer Text gehört auch nicht in ein Meldungsfeld: Er wird in einer Ablage hingelegt
    # (wie ein Messwert in seiner Reihe), und gemeldet wird der Verweis darauf. Das steht vor
    # der Melde-Frage, denn auch ein stiller Job soll behalten, was er erarbeitet hat.
    if job.result_html:
        nodes.insert(2, _n("ablegen", "auto_action", 2, _action(
            "document", "In die Ablage legen", storage=_key(job.name), name=job.name,
            text=result_text, format="markdown"), spalte=1))
        edges = [_e("start", "arbeit"), _e("arbeit", "ablegen"), _e("ablegen", "answer")]

    report = job.notify_mode or "always"
    if report == "never":
        nodes.append(_n("fertig", "end", 3, {"label": "Fertig", "outcome": "completed"}))
        edges.append(_e("answer", "fertig"))
        return {"nodes": nodes, "edges": edges}

    text = "{{ document.title }}\n{{ document.url }}" if job.result_html else result_text
    melde_node = _n("melden", "auto_action", 4, _action(
        "notify", "Bescheid geben", kind="job", title=f"Job: {job.name}", text=text), spalte=-1)

    if report == "always":
        nodes += [melde_node, _n("fertig", "end", 5, {"label": "Gemeldet", "outcome": "completed"})]
        edges += [_e("answer", "melden"), _e("melden", "fertig")]
        return {"nodes": nodes, "edges": edges}

    # on_output / on_error: erst hinsehen, dann melden.
    if report == "on_error":
        bedingung, label = error_bedingung, "fehlgeschlagen"
    else:
        bedingung, label = {"!=": [{"var": "answer"}, ""]}, "hat etwas gesagt"
    nodes += [
        _n("melden_wenn", "decision", 3, {
            "label": "Melden?",
            "branches": [{"handle": "melden", "label": label, "guard": bedingung},
                         {"handle": "still", "label": "still bleiben"}],
            "default_handle": "still"}),
        melde_node,
        _n("fertig", "end", 5, {"label": "Fertig", "outcome": "completed"}),
    ]
    edges += [_e("answer", "melden_wenn"), _e("melden_wenn", "melden", "melden"),
              _e("melden_wenn", "fertig", "still", "ohne Nachricht"), _e("melden", "fertig")]
    return {"nodes": nodes, "edges": edges}


async def as_flow(db: AsyncSession, job: Job) -> None:
    """Diesen einen Job auf einen Ablauf umstellen (ohne commit).

    Auch beim Anlegen benutzt: Wer einen Job über die API, das Agenten-Werkzeug oder eine
    Vorlage einträgt, bekommt keinen alten Weg mehr aufgemacht, den ein späterer Neustart
    dann wieder einsammeln müsste.
    """
    import datetime as dt

    target_name = ""
    if job.kind == "http" and job.destination_id:
        # Der Aufruf im Ablauf nennt den Namen, nicht die Nummer — Ziele werden über den
        # Namen aufgelöst (Projekt, dann Nutzer, dann systemweit).
        from ..models.destination import Destination
        target = await db.get(Destination, job.destination_id)
        target_name = target.name if target else ""
    # Name und Schlüssel beschreiben die Sache, nicht den Auslöser: Der Job heißt schon so,
    # wie das gemeint ist, was er tut — „KI- & Tech-News“, nicht „Job: 3“.
    from .workflow_templates import freier_key
    d = WorkflowDefinition(
        project_id=job.project_id,
        key=await freier_key(db, job.name, job.project_id), name=job.name,
        description=f"Aus der Job-Art „{job.kind or 'prompt'}“ umgestellt.",
        subject_kind=WorkflowSubjectKind.standalone, enabled=True, created_by=job.user_id)
    db.add(d)
    await db.flush()
    version = WorkflowVersion(
        definition_id=d.id, version=1, graph=_graph(job, target_name), created_by=job.user_id,
        status=WorkflowVersionStatus.published,
        published_at=dt.datetime.now(tz=dt.timezone.utc),
        notes="Umstellung der Job-Arten auf Abläufe")
    db.add(version)
    await db.flush()
    d.current_version_id = version.id
    log.info("Job %s (%s) läuft jetzt über den Ablauf %s", job.name, job.kind or "prompt", d.key)
    job.kind = "workflow"
    job.workflow_definition_id = d.id


async def convert(db: AsyncSession) -> int:
    """Stellt jeden Job um, der noch eine alte Art trägt. Gibt die Anzahl zurück."""
    jobs = (await db.execute(select(Job).where(Job.kind.in_(ALTE_KINDS)))).scalars().all()
    for job in jobs:
        await as_flow(db, job)
    if jobs:
        await db.commit()
    return len(jobs)
