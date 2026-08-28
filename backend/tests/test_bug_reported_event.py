"""An incoming report is an occasion, not just a row.

`create_report` created the report and was done. Whoever wanted to hear about it had to
remember to look in the list, and nobody does that reliably. Now Traccoon emits
`bug.reported`, and every flow with the matching trigger hangs itself on it: a message to a
phone, a ticket straight away, a note in the vault.

Two things are pinned down here, because otherwise they break silently:

1. **The event comes AFTER the commit.** The flow starts at once and reads the report; if
   the event stood before it, the flow would read a row that does not exist yet.
2. **A broken flow must not eat the report.** The sender is somebody else's program and has
   exactly one attempt. Better a flow that does not run than a report nobody ever saw.
"""
import pytest
from sqlalchemy import select

from app.models.artifact import Artifact
from app.models.bugs import BugSource
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import bugs

from conftest import auth, make_project, make_user


async def _source(db, *, project=None) -> BugSource:
    if project is None:
        project = await make_project(db, "DEV", "Device programmer")
    source = BugSource(key="devprog", name="Device programmer", enabled=True,
                       project_id=project.id)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def _listener(db, *, key: str = "melder", trigger: dict | None = None) -> WorkflowDefinition:
    """A standalone flow that listens for the event and does nothing else."""
    graph = {
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
             "data": {"config": {"label": "Start",
                                 "trigger": trigger or {"event": "bug.reported"}}}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
             "data": {"config": {"outcome": "completed"}}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    d = WorkflowDefinition(project_id=None, key=key, name=key, enabled=True,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def _instances(db) -> list[WorkflowInstance]:
    return list((await db.execute(select(WorkflowInstance))).scalars().all())


async def test_a_report_starts_the_listening_flow(db):
    await _listener(db)
    source = await _source(db)

    await bugs.create_report(db, source, {
        "title": "Der Import bricht bei Kanal 200 ab",
        "kind": "bug", "version": "2.1.0", "contact": "dl1abc@example.org",
        "details": "Nach dem Klick auf Importieren passiert nichts mehr."})

    instances = await _instances(db)
    assert len(instances) == 1
    report = instances[0].context["report"]
    # What the message needs stands in the context: otherwise the flow would first have to
    # look the report up to be able to say what it is about.
    assert report["title"] == "Der Import bricht bei Kanal 200 ab"
    assert report["app"] == "devprog" and report["program"] == "Device programmer"
    assert report["kind"] == "bug" and report["version"] == "2.1.0"
    assert report["contact"] == "dl1abc@example.org"
    assert "Importieren" in report["details"]
    assert instances[0].context["event"]["name"] == "bug.reported"


async def test_the_report_exists_when_the_flow_reads_it(db):
    """The event comes after the commit, otherwise the flow reads a row that does not exist
    yet."""
    await _listener(db)
    source = await _source(db)
    artifact = await bugs.create_report(db, source, {"title": "Da fehlt was"})

    instance = (await _instances(db))[0]
    assert instance.context["report"]["id"] == artifact.id
    assert await db.get(Artifact, artifact.id) is not None


async def test_a_broken_flow_does_not_swallow_the_report(db, monkeypatch):
    """The sender is somebody else's program and has exactly one attempt."""
    async def broken(*a, **kw):
        raise RuntimeError("the flow is broken")

    monkeypatch.setattr("app.services.events.emit", broken)
    source = await _source(db)

    artifact = await bugs.create_report(db, source, {"title": "Trotzdem angekommen"})
    assert artifact.id and artifact.title == "Trotzdem angekommen"
    assert await db.get(Artifact, artifact.id) is not None


async def test_a_flow_of_another_project_stays_out_of_it(db):
    """The report of a program belongs to the project that serves that program."""
    mine = await make_project(db, "AAA", "Meins")
    other = await make_project(db, "BBB", "Fremd")
    await _listener(db, key="nur_fremd",
                    trigger={"event": "bug.reported", "project_id": other.id})
    await _listener(db, key="ueberall")

    await bugs.create_report(db, await _source(db, project=mine), {"title": "Hallo"})
    instances = await _instances(db)
    assert len(instances) == 1
    assert (await db.get(WorkflowDefinition, instances[0].definition_id)).key == "ueberall"


async def test_the_event_is_in_the_picker():
    """Without this entry the event does not appear in the editor, and whoever builds the flow
    has to guess the name."""
    from app.services.events import BUILTIN_EVENTS
    assert dict(BUILTIN_EVENTS)["bug.reported"]


# ── Filtern, ohne JSONLogic zu tippen ────────────────────────────────────────

async def test_only_the_ticked_kinds_start_the_flow(db):
    """Not every report is a bug. Whoever only wants the bugs on their phone ticks them, and
    does not have to write a condition for it."""
    await _listener(db, key="nur_fehler",
                    trigger={"event": "bug.reported", "where": {"report.kind": ["bug"]}})
    source = await _source(db)

    await bugs.create_report(db, source, {"title": "Ein Wunsch", "kind": "feature"})
    assert await _instances(db) == []

    await bugs.create_report(db, source, {"title": "Etwas ist kaputt", "kind": "bug"})
    assert len(await _instances(db)) == 1


async def test_several_ticked_kinds_are_an_or(db):
    await _listener(db, key="fehler_und_fragen",
                    trigger={"event": "bug.reported",
                             "where": {"report.kind": ["bug", "question"]}})
    source = await _source(db)

    for kind in ("bug", "question", "feature"):
        await bugs.create_report(db, source, {"title": kind, "kind": kind})
    started = await _instances(db)
    assert len(started) == 2
    assert {i.context["report"]["kind"] for i in started} == {"bug", "question"}


async def test_nothing_ticked_means_everything(db):
    """An empty entry must not silence the flow: otherwise removing the last tick switches
    the flow off without anybody having said so."""
    await _listener(db, key="alles",
                    trigger={"event": "bug.reported", "where": {"report.kind": []}})
    await bugs.create_report(db, await _source(db), {"title": "Egal", "kind": "feature"})
    assert len(await _instances(db)) == 1


async def test_the_selection_and_a_handwritten_condition_both_have_to_hold(db):
    """`where` belongs to the editor, `filter` to the person. Neither may override the
    other."""
    await _listener(db, key="beides", trigger={
        "event": "bug.reported",
        "where": {"report.kind": ["bug"]},
        "filter": {"==": [{"var": "report.app"}, "devprog"]}})
    source = await _source(db)

    await bugs.create_report(db, source, {"title": "richtige Art, richtiges Programm",
                                          "kind": "bug"})
    assert len(await _instances(db)) == 1

    await bugs.create_report(db, source, {"title": "falsche Art", "kind": "feature"})
    assert len(await _instances(db)) == 1     # nichts dazugekommen


async def test_the_kinds_are_offered_in_the_editor(db, client):
    """Without this route one would have to guess the path `report.kind`, and a wrongly
    guessed filter is silent: the flow simply never starts."""
    anna = await make_user(db, "anna")
    r = await client.get("/workflow-events", headers=auth(anna))
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json() if e["event"] == "bug.reported")
    field = entry["fields"][0]
    assert field["path"] == "report.kind"
    assert {o["value"] for o in field["options"]} == {"bug", "feature", "question"}
    # And an event without filter fields delivers an empty list instead of nothing at all.
    assert next(e for e in r.json() if e["event"] == "issue.created")["fields"] == []
