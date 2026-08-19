"""Artifacts: a register for ticket, hardware and own types, and ONE way to set a state.

Before there were three status actions of which two were pointless depending on the flow,
and the possible values stood only in the code.
"""
import pytest
from app.models.artifact import ArtifactFieldOption, ArtifactType
from app.models.enums import (
    PurchaseStatus, StatusCategory, TicketAgentStatus, WorkflowSubjectKind,
)
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import artifact_fields as felder
from app.services import artifacts as art
from sqlalchemy import select
from conftest import auth, make_asset, make_project, make_user


@pytest.fixture
async def register(db):
    await art.ensure_builtin_types(db)
    return db


async def test_eingebaute_typen_decken_die_echten_zustaende_ab(db, register):
    """The keys MUST correspond to the enum values: they are stored that way."""
    ticket = await art.type_by_key(db, "ticket")
    hardware = await art.type_by_key(db, "hardware")
    ticket_keys = {s.value for s in await art.statuses(db, ticket.id)}
    hw_keys = {s.value for s in await art.statuses(db, hardware.id)}

    assert ticket_keys == {s.value for s in TicketAgentStatus}
    assert hw_keys == {s.value for s in PurchaseStatus}
    assert ticket.backing == "issue" and hardware.backing == "hardware_asset"


async def test_subjekt_findet_seinen_typ(db, register):
    assert (await art.type_for_subject(db, WorkflowSubjectKind.issue)).key == "ticket"
    assert (await art.type_for_subject(db, "hardware_asset")).key == "hardware"
    assert await art.type_for_subject(db, WorkflowSubjectKind.standalone) is None


async def test_zustand_setzen_wirkt_auf_das_ticket(db, register):
    proj = await make_project(db, "TST", "Test")
    t = IssueType(project_id=proj.id, name="Aufgabe")
    for i, (name, kat) in enumerate([("To Do", StatusCategory.todo),
                                     ("Warten", StatusCategory.in_progress)]):
        db.add(WorkflowStatus(project_id=proj.id, name=name, category=kat, order=i))
    db.add_all([t, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    spalten = {s.name: s for s in (await db.execute(
        select(WorkflowStatus).where(WorkflowStatus.project_id == proj.id))).scalars().all()}
    issue = Issue(project_id=proj.id, number=1, key="TST-1", type_id=t.id,
                  status_id=spalten["To Do"].id, summary="X", reporter_id=1, rank="1")
    db.add(issue)
    await db.commit()

    await art.apply_status(db, subject_kind=WorkflowSubjectKind.issue, issue=issue,
                           status_key="hold", reason="merge")
    assert issue.agent_status == TicketAgentStatus.hold
    assert issue.hold_reason.value == "merge"
    assert issue.status_id == spalten["Warten"].id      # the board column follows


async def test_zustand_setzen_wirkt_auf_die_hardware(db, register):
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Switch", project=proj)
    await art.apply_status(db, subject_kind=WorkflowSubjectKind.hardware_asset, asset=asset,
                           status_key="delivered")
    assert asset.purchase_status == PurchaseStatus.delivered
    assert asset.delivery_date is not None       # the date is carried along


async def test_unbekannter_zustand_wird_abgewiesen(db, register):
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Switch", project=proj)
    with pytest.raises(ValueError, match="is not a state"):
        await art.apply_status(db, subject_kind=WorkflowSubjectKind.hardware_asset,
                               asset=asset, status_key="gibtsnicht")


async def test_seed_ist_idempotent_und_behaelt_beschriftungen(db, register):
    ticket = await art.type_by_key(db, "ticket")
    feld = await felder.status_field(db, ticket.id)
    s = (await db.execute(select(ArtifactFieldOption).where(
        ArtifactFieldOption.field_id == feld.id,
        ArtifactFieldOption.value == "hold"))).scalar_one()
    s.label = "Wartet auf mich"
    await db.commit()

    await art.ensure_builtin_types(db)
    await db.refresh(s)
    assert s.label == "Wartet auf mich"
    assert len(await art.statuses(db, ticket.id)) == len(TicketAgentStatus)


async def test_nur_admin_pflegt_typen(client, db, register):
    normal = await make_user(db, "otto")
    chef = await make_user(db, "chef", admin=True)

    r = await client.get("/artifact-types", headers=auth(normal))
    assert r.status_code == 200 and len(r.json()) == 2      # everybody may read

    r = await client.post("/artifact-types", headers=auth(normal),
                          json={"key": "vertrag", "name": "Vertrag"})
    assert r.status_code == 403

    r = await client.post("/artifact-types", headers=auth(chef),
                          json={"key": "vertrag", "name": "Vertrag", "icon": "📄"})
    assert r.status_code == 201, r.text
    assert r.json()["backing"] == "generic" and r.json()["builtin"] is False


async def test_eingebauter_typ_laesst_sich_nicht_loeschen(client, db, register):
    chef = await make_user(db, "chef", admin=True)
    ticket = await art.type_by_key(db, "ticket")
    r = await client.delete(f"/artifact-types/{ticket.id}", headers=auth(chef))
    assert r.status_code == 409
    assert "cannot be deleted" in r.json()["detail"]


async def test_beschriftung_eines_eingebauten_zustands_ist_aenderbar(client, db, register):
    """The key stays (it IS the stored value), the label does not."""
    chef = await make_user(db, "chef", admin=True)
    ticket = await art.type_by_key(db, "ticket")
    s = next(x for x in await art.statuses(db, ticket.id) if x.value == "to_test")
    r = await client.put(f"/artifact-field-options/{s.id}", headers=auth(chef),
                         json={"key": "GEAENDERT", "label": "Warte auf Abnahme",
                               "category": "in_progress", "order": 5, "waiting": True})
    assert r.status_code == 200
    assert r.json()["label"] == "Warte auf Abnahme"
    assert r.json()["value"] == "to_test"        # unchanged


async def test_uebergreifende_liste_zeigt_ticket_und_hardware(client, db, register):
    """The actual gain: "what is pending?" over both worlds in one query."""
    from app.models.enums import ProjectRole, StatusCategory
    from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    from conftest import add_member

    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)
    t = IssueType(project_id=proj.id, name="Aufgabe")
    s = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([t, s, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key="TST-1", type_id=t.id, status_id=s.id,
                  summary="Wartet auf mich", reporter_id=owner.id, rank="0001",
                  agent_status=TicketAgentStatus.plan_review)
    db.add(issue)
    asset = await make_asset(db, "Switch", project=proj)
    await db.commit()
    await art.reconcile(db)
    await art.ensure_for_asset(db, asset)
    await db.commit()

    r = await client.get("/artifacts", headers=auth(owner))
    assert r.status_code == 200, r.text
    typen = {a["type_key"] for a in r.json()}
    assert typen == {"ticket", "hardware"}

    # Only what waits for a human: the register says which states those are.
    r = await client.get("/artifacts?waiting=true", headers=auth(owner))
    wartend = r.json()
    assert [a["ref"] for a in wartend] == ["TST-1"]
    assert wartend[0]["status_label"] == "Plan wartet auf Freigabe"


async def test_fremde_projekte_bleiben_unsichtbar(client, db, register):
    from app.models.enums import ProjectRole, StatusCategory
    from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    from conftest import add_member

    ich = await make_user(db, "ich")
    fremd = await make_project(db, "FRD", "Fremd")
    t = IssueType(project_id=fremd.id, name="Aufgabe")
    s = WorkflowStatus(project_id=fremd.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([t, s, IssueCounter(project_id=fremd.id, last_number=0)])
    await db.commit()
    db.add(Issue(project_id=fremd.id, number=1, key="FRD-1", type_id=t.id, status_id=s.id,
                 summary="Geheim", reporter_id=1, rank="0001"))
    await db.commit()
    await art.reconcile(db)

    meins = await make_project(db, "MIN", "Meins")
    await add_member(db, meins, ich, ProjectRole.owner)
    r = await client.get("/artifacts", headers=auth(ich))
    assert [a["title"] for a in r.json()] == []
