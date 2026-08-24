"""Eine eingegangene Meldung ist ein Anlass, nicht nur eine Zeile.

`create_report` legte die Meldung an und war fertig. Wer davon erfahren wollte, musste sich
merken, in die Liste zu schauen — und genau das tut niemand zuverlässig. Jetzt meldet
Traccoon `bug.reported`, und jeder Ablauf mit dem passenden Auslöser hängt sich dran: eine
Nachricht aufs Telefon, sofort ein Ticket, eine Notiz im Vault.

Zwei Dinge sind hier festgenagelt, weil sie sonst still kaputtgehen:

1. **Das Ereignis kommt NACH dem Commit.** Der Ablauf startet sofort und liest die Meldung;
   stünde das Ereignis davor, läse er eine Zeile, die es noch nicht gibt.
2. **Ein kaputter Ablauf darf die Meldung nicht fressen.** Der Absender ist das Programm
   eines Fremden und hat genau einen Versuch. Lieber ein Ablauf, der nicht läuft, als eine
   Meldung, die niemand je gesehen hat.
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
    source = BugSource(key="devprog", name="Devprog-Programmer", enabled=True,
                       project_id=project.id if project else None)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def _listener(db, *, key: str = "melder", trigger: dict | None = None) -> WorkflowDefinition:
    """Ein freistehender Ablauf, der auf das Ereignis hört und sonst nichts tut."""
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
    # Was die Nachricht braucht, steht im Kontext — sonst müsste der Ablauf die Meldung
    # erst nachschlagen, um sagen zu können, worum es geht.
    assert report["title"] == "Der Import bricht bei Kanal 200 ab"
    assert report["app"] == "devprog" and report["program"] == "Devprog-Programmer"
    assert report["kind"] == "bug" and report["version"] == "2.1.0"
    assert report["contact"] == "dl1abc@example.org"
    assert "Importieren" in report["details"]
    assert instances[0].context["event"]["name"] == "bug.reported"


async def test_the_report_exists_when_the_flow_reads_it(db):
    """Das Ereignis kommt nach dem Commit — sonst liest der Ablauf eine Zeile, die es noch
    nicht gibt."""
    await _listener(db)
    source = await _source(db)
    artifact = await bugs.create_report(db, source, {"title": "Da fehlt was"})

    instance = (await _instances(db))[0]
    assert instance.context["report"]["id"] == artifact.id
    assert await db.get(Artifact, artifact.id) is not None


async def test_a_broken_flow_does_not_swallow_the_report(db, monkeypatch):
    """Der Absender ist das Programm eines Fremden und hat genau einen Versuch."""
    async def broken(*a, **kw):
        raise RuntimeError("der Ablauf ist kaputt")

    monkeypatch.setattr("app.services.events.emit", broken)
    source = await _source(db)

    artifact = await bugs.create_report(db, source, {"title": "Trotzdem angekommen"})
    assert artifact.id and artifact.title == "Trotzdem angekommen"
    assert await db.get(Artifact, artifact.id) is not None


async def test_a_flow_of_another_project_stays_out_of_it(db):
    """Die Meldung eines Programms gehört dem Projekt, das das Programm bedient."""
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
    """Ohne diesen Eintrag steht das Ereignis im Editor nicht zur Auswahl, und wer den Ablauf
    baut, muss den Namen raten."""
    from app.services.events import BUILTIN_EVENTS
    assert dict(BUILTIN_EVENTS)["bug.reported"]


# ── Filtern, ohne JSONLogic zu tippen ────────────────────────────────────────

async def test_only_the_ticked_kinds_start_the_flow(db):
    """Nicht jede Meldung ist ein Fehler. Wer nur die Fehler aufs Telefon will, hakt sie an
    — und muss dafür keine Bedingung schreiben."""
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
    """Ein leerer Eintrag darf den Ablauf nicht stumm schalten — sonst schaltet das
    Wegnehmen des letzten Hakens den Ablauf ab, ohne dass jemand das gesagt hat."""
    await _listener(db, key="alles",
                    trigger={"event": "bug.reported", "where": {"report.kind": []}})
    await bugs.create_report(db, await _source(db), {"title": "Egal", "kind": "feature"})
    assert len(await _instances(db)) == 1


async def test_the_selection_and_a_handwritten_condition_both_have_to_hold(db):
    """`where` gehört dem Editor, `filter` dem Menschen. Eines darf das andere nicht
    aushebeln."""
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
    """Ohne diesen Weg müsste man den Pfad `report.kind` raten — und ein falsch geratener
    Filter ist still: der Ablauf startet einfach nie."""
    anna = await make_user(db, "anna")
    r = await client.get("/workflow-events", headers=auth(anna))
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json() if e["event"] == "bug.reported")
    field = entry["fields"][0]
    assert field["path"] == "report.kind"
    assert {o["value"] for o in field["options"]} == {"bug", "feature", "question"}
    # Und ein Ereignis ohne Filterfelder liefert eine leere Liste statt gar nichts.
    assert next(e for e in r.json() if e["event"] == "issue.created")["fields"] == []
