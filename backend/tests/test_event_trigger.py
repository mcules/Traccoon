"""Event triggers: the flow decides what it listens for, not the trigger.

Until now a webhook or a job had to name a particular definition. Now Traccoon reports an
event, and every flow with a matching trigger on its start node starts up.
"""
from app.models.enums import (
    ProjectRole, WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.events import emit, listeners
from sqlalchemy import select
from conftest import add_member, auth, make_project, make_user


async def _ablauf(db, *, key: str, trigger: dict | None, project_id=None,
                  subject=WorkflowSubjectKind.standalone) -> WorkflowDefinition:
    start_cfg = {"label": "Start"}
    if trigger:
        start_cfg["trigger"] = trigger
    graph = {
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
             "data": {"config": start_cfg}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
             "data": {"config": {"outcome": "completed"}}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    d = WorkflowDefinition(project_id=project_id, key=key, name=key, subject_kind=subject)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def _instanzen(db) -> list[WorkflowInstance]:
    return list((await db.execute(select(WorkflowInstance))).scalars().all())


async def test_ereignis_startet_alle_zuhoerer(db):
    await _ablauf(db, key="a", trigger={"event": "mail.received"})
    await _ablauf(db, key="b", trigger={"event": "mail.received"})
    await _ablauf(db, key="c", trigger={"event": "issue.created"})
    await _ablauf(db, key="d", trigger=None)          # no trigger, so manual only

    ids = await emit(db, "mail.received", payload={"betreff": "Rechnung"})
    assert len(ids) == 2
    inst = await _instanzen(db)
    assert {i.definition_id for i in inst} == {
        d.id for d in (await db.execute(select(WorkflowDefinition)
                                        .where(WorkflowDefinition.key.in_(["a", "b"])))).scalars()}
    # The content of the event is available to the flow.
    assert inst[0].context["betreff"] == "Rechnung"
    assert inst[0].context["event"]["name"] == "mail.received"


async def test_trigger_auf_ein_projekt_begrenzt(db):
    p1 = await make_project(db, "AAA", "Eins")
    p2 = await make_project(db, "BBB", "Zwei")
    await _ablauf(db, key="nur_p1", trigger={"event": "x", "project_id": p1.id})
    await _ablauf(db, key="ueberall", trigger={"event": "x"})

    assert len(await emit(db, "x", project_id=p2.id)) == 1      # only the unbound one
    assert len(await emit(db, "x", project_id=p1.id)) == 2      # beide


async def test_projektgebundener_ablauf_hoert_nur_auf_sein_projekt(db):
    p1 = await make_project(db, "AAA", "Eins")
    p2 = await make_project(db, "BBB", "Zwei")
    await _ablauf(db, key="im_p1", trigger={"event": "x"}, project_id=p1.id)

    assert await listeners(db, "x", p2.id) == []
    assert len(await listeners(db, "x", p1.id)) == 1


async def test_bedingung_filtert(db):
    await _ablauf(db, key="nur_dringend", trigger={
        "event": "issue.created",
        "filter": {"==": [{"var": "issue.priority"}, "highest"]},
    })
    assert await emit(db, "issue.created", payload={"issue": {"priority": "low"}}) == []
    assert len(await emit(db, "issue.created", payload={"issue": {"priority": "highest"}})) == 1


async def test_doppelte_meldung_startet_nur_einmal(db):
    await _ablauf(db, key="a", trigger={"event": "mail.received"})
    erst = await emit(db, "mail.received", source_ref="uid-42")
    zweit = await emit(db, "mail.received", source_ref="uid-42")
    assert len(erst) == 1 and zweit == []


async def test_kaputter_ablauf_stoppt_die_anderen_nicht(db):
    """An event is a report, not an assignment: a broken listener must neither tear the
    trigger nor the other flows with it."""
    kaputt = await _ablauf(db, key="kaputt", trigger={"event": "x"})
    v = await db.get(WorkflowVersion, kaputt.current_version_id)
    v.graph = {"nodes": [], "edges": []}          # no start node any more
    await db.commit()
    await _ablauf(db, key="heil", trigger={"event": "x"})

    ids = await emit(db, "x")
    assert len(ids) == 1


async def test_ticket_anlegen_meldet_das_ereignis(client, db, seeded):
    """The most important connection: a new ticket triggers `issue.created`."""
    from app.models.enums import StatusCategory
    from app.models.ticket import IssueCounter, IssueType, WorkflowStatus

    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)
    db.add_all([
        IssueType(project_id=proj.id, name="Aufgabe"),
        WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0),
        IssueCounter(project_id=proj.id, last_number=0),
    ])
    await db.commit()
    await _ablauf(db, key="auf_neues_ticket", trigger={"event": "issue.created"})

    r = await client.post(f"/projects/{proj.id}/issues", headers=auth(owner),
                          json={"summary": "Neu"})
    assert r.status_code == 201, r.text
    inst = await _instanzen(db)
    assert len(inst) == 1
    assert inst[0].context["issue"]["key"] == r.json()["key"]
    assert inst[0].status == WorkflowInstanceStatus.completed
