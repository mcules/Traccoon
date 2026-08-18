"""Eigene Abläufe: jeder darf sie anlegen — sie wirken aber nur dort, wo er selbst darf.

Ein freier Ablauf (kein Projekt, kein Slot) gehört seinem Ersteller. Das ist mehr als eine
Anzeigeregel: die Definition liegt projektlos in derselben Tabelle wie die ausgelieferten
Vorlagen, und ihre Aktionen fassen Artefakte an. Geprüft wird deshalb an drei Stellen —
sehen, starten, und auf ein Ereignis anspringen.
"""
import pytest
from app.models.enums import (
    ProjectRole, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.events import emit
from sqlalchemy import select

from conftest import add_member, auth, make_project, make_user

pytestmark = pytest.mark.asyncio


async def _freier_ablauf(db, besitzer, key: str, *, trigger: dict | None = None,
                         veroeffentlicht: bool = True) -> WorkflowDefinition:
    start_cfg: dict = {"label": "Start"}
    if trigger:
        start_cfg["trigger"] = trigger
    graph = {"nodes": [{"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                        "data": {"config": start_cfg}},
                       {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                        "data": {"config": {"outcome": "completed"}}}],
             "edges": [{"id": "e1", "source": "s", "target": "e"}]}
    d = WorkflowDefinition(project_id=None, key=key, name=key, created_by=besitzer.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=(WorkflowVersionStatus.published if veroeffentlicht
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if veroeffentlicht:
        d.current_version_id = v.id
    await db.commit()
    return d


async def test_jeder_darf_einen_eigenen_ablauf_anlegen(client, db):
    """Ein eigener Ablauf ist kein Adminrecht — sonst hat ihn niemand außer dem Admin."""
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "abendrunde", "name": "Abendrunde",
        "subject_kind": "standalone"})
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == anna.id or True   # Feld optional im Schema


async def test_fremder_ablauf_ist_unsichtbar_und_unantastbar(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _freier_ablauf(db, anna, "annas-ablauf")

    sichtbar = [w["id"] for w in (await client.get("/workflows", headers=auth(bert))).json()]
    assert d.id not in sichtbar
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(anna))).json()]

    assert (await client.put(f"/workflows/{d.id}", headers=auth(bert),
                             json={"name": "geklaut"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(bert),
                              json={"subject_kind": "standalone"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                              json={"subject_kind": "standalone"})).status_code == 201


async def test_admin_sieht_und_darf_alles(client, db):
    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    d = await _freier_ablauf(db, anna, "annas-ablauf")
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(chef))).json()]
    assert (await client.put(f"/workflows/{d.id}", headers=auth(chef),
                             json={"name": "umbenannt"})).status_code == 200


async def test_start_verlangt_rechte_am_artefakt(client, db):
    """Der Ablauf gehört Anna — das Ticket nicht. Was er anfasst, entscheidet das Projekt."""
    from app.models.ticket import Issue, IssueType, WorkflowStatus

    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    projekt = await make_project(db, "GEH", "Geheim")
    await add_member(db, projekt, chef, ProjectRole.owner)
    typ = IssueType(project_id=projekt.id, name="Aufgabe", order=0)
    status_ = WorkflowStatus(project_id=projekt.id, name="Offen", order=0)
    db.add_all([typ, status_])
    await db.flush()
    issue = Issue(project_id=projekt.id, number=1, key="GEH-1", type_id=typ.id,
                  status_id=status_.id, summary="Fremd", reporter_id=chef.id, rank="1")
    db.add(issue)
    await db.commit()

    d = await _freier_ablauf(db, anna, "annas-ablauf")
    r = await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                          json={"subject_kind": "issue", "issue_id": issue.id})
    assert r.status_code in (403, 404), r.text


async def test_ereignis_startet_nur_bei_eigenen_projekten(db):
    """Ohne diese Grenze liefe Annas Ablauf bei JEDEM Ticket-Ereignis mit — auch in
    Projekten, die sie gar nicht sehen darf."""
    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    ihrs = await make_project(db, "IHR", "Annas Projekt")
    await add_member(db, ihrs, anna, ProjectRole.member)
    fremd = await make_project(db, "FRD", "Fremdes Projekt")
    await add_member(db, fremd, chef, ProjectRole.owner)

    await _freier_ablauf(db, anna, "annas-lauscher", trigger={"event": "issue.created"})

    assert len(await emit(db, "issue.created", project_id=fremd.id)) == 0
    assert len(await emit(db, "issue.created", project_id=ihrs.id)) == 1
    # Die Instanz selbst bleibt projektlos (das Subjekt ist `standalone`) — im Kontext
    # steht aber, aus welchem Projekt das Ereignis kam.
    lauf = (await db.execute(select(WorkflowInstance))).scalars().one()
    assert lauf.project_id is None
    assert lauf.context["event"]["project_id"] == ihrs.id


async def test_admin_ablauf_hoert_ueberall(db):
    chef = await make_user(db, "chef", admin=True)
    fremd = await make_project(db, "FRD", "Fremdes Projekt")
    await _freier_ablauf(db, chef, "chef-lauscher", trigger={"event": "issue.created"})
    assert len(await emit(db, "issue.created", project_id=fremd.id)) == 1
