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
from app.services import artifact_fields as fields
from app.services import artifacts as kind
from sqlalchemy import select
from conftest import auth, make_asset, make_project, make_user


@pytest.fixture
async def register(db):
    await kind.ensure_builtin_types(db)
    return db


async def test_builtin_types_cover_the_real_states(db, register):
    """The keys MUST correspond to the enum values: they are stored that way."""
    ticket = await kind.type_by_key(db, "ticket")
    hardware = await kind.type_by_key(db, "hardware")
    ticket_keys = {s.value for s in await kind.statuses(db, ticket.id)}
    hw_keys = {s.value for s in await kind.statuses(db, hardware.id)}

    assert ticket_keys == {s.value for s in TicketAgentStatus}
    assert hw_keys == {s.value for s in PurchaseStatus}
    assert ticket.backing == "issue" and hardware.backing == "hardware_asset"


async def test_subject_finds_its_kind(db, register):
    assert (await kind.type_for_subject(db, WorkflowSubjectKind.issue)).key == "ticket"
    assert (await kind.type_for_subject(db, "hardware_asset")).key == "hardware"
    assert await kind.type_for_subject(db, WorkflowSubjectKind.standalone) is None


async def test_setting_state_affects_the_ticket(db, register):
    proj = await make_project(db, "TST", "Test")
    t = IssueType(project_id=proj.id, name="Aufgabe")
    for i, (name, category) in enumerate([("To Do", StatusCategory.todo),
                                     ("Warten", StatusCategory.in_progress)]):
        db.add(WorkflowStatus(project_id=proj.id, name=name, category=category, order=i))
    db.add_all([t, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    columns = {s.name: s for s in (await db.execute(
        select(WorkflowStatus).where(WorkflowStatus.project_id == proj.id))).scalars().all()}
    issue = Issue(project_id=proj.id, number=1, key="TST-1", type_id=t.id,
                  status_id=columns["To Do"].id, summary="X", reporter_id=1, rank="1")
    db.add(issue)
    await db.commit()

    await kind.apply_status(db, subject_kind=WorkflowSubjectKind.issue, issue=issue,
                           status_key="hold", reason="merge")
    assert issue.agent_status == TicketAgentStatus.hold
    assert issue.hold_reason.value == "merge"
    assert issue.status_id == columns["Warten"].id      # the board column follows


async def test_setting_state_affects_the_hardware(db, register):
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Switch", project=proj)
    await kind.apply_status(db, subject_kind=WorkflowSubjectKind.hardware_asset, asset=asset,
                           status_key="delivered")
    assert asset.purchase_status == PurchaseStatus.delivered
    assert asset.delivery_date is not None       # the date is carried along


async def test_unknown_state_is_rejected(db, register):
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Switch", project=proj)
    with pytest.raises(ValueError, match="is not a state"):
        await kind.apply_status(db, subject_kind=WorkflowSubjectKind.hardware_asset,
                               asset=asset, status_key="gibtsnicht")


async def test_seed_is_idempotent_and_keeps_labels(db, register):
    ticket = await kind.type_by_key(db, "ticket")
    field = await fields.status_field(db, ticket.id)
    s = (await db.execute(select(ArtifactFieldOption).where(
        ArtifactFieldOption.field_id == field.id,
        ArtifactFieldOption.value == "hold"))).scalar_one()
    s.label = "Wartet auf mich"
    await db.commit()

    await kind.ensure_builtin_types(db)
    await db.refresh(s)
    assert s.label == "Wartet auf mich"
    assert len(await kind.statuses(db, ticket.id)) == len(TicketAgentStatus)


async def test_only_an_admin_curates_types(client, db, register):
    normal = await make_user(db, "otto")
    boss = await make_user(db, "chef", admin=True)

    r = await client.get("/artifact-types", headers=auth(normal))
    assert r.status_code == 200 and len(r.json()) == 2      # everybody may read

    r = await client.post("/artifact-types", headers=auth(normal),
                          json={"key": "vertrag", "name": "Vertrag"})
    assert r.status_code == 403

    r = await client.post("/artifact-types", headers=auth(boss),
                          json={"key": "vertrag", "name": "Vertrag", "icon": "📄"})
    assert r.status_code == 201, r.text
    assert r.json()["backing"] == "generic" and r.json()["builtin"] is False


async def test_builtin_kind_cannot_be_deleted(client, db, register):
    boss = await make_user(db, "chef", admin=True)
    ticket = await kind.type_by_key(db, "ticket")
    r = await client.delete(f"/artifact-types/{ticket.id}", headers=auth(boss))
    assert r.status_code == 409
    assert "cannot be deleted" in r.json()["detail"]


async def test_label_of_a_builtin_state_can_be_changed(client, db, register):
    """The key stays (it IS the stored value), the label does not."""
    boss = await make_user(db, "chef", admin=True)
    ticket = await kind.type_by_key(db, "ticket")
    s = next(x for x in await kind.statuses(db, ticket.id) if x.value == "to_test")
    r = await client.put(f"/artifact-field-options/{s.id}", headers=auth(boss),
                         json={"key": "GEAENDERT", "label": "Warte auf Abnahme",
                               "category": "in_progress", "order": 5, "waiting": True})
    assert r.status_code == 200
    assert r.json()["label"] == "Warte auf Abnahme"
    assert r.json()["value"] == "to_test"        # unchanged


async def test_combined_listing_shows_ticket_and_hardware(client, db, register):
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
    await kind.reconcile(db)
    await kind.ensure_for_asset(db, asset)
    await db.commit()

    r = await client.get("/artifacts", headers=auth(owner))
    assert r.status_code == 200, r.text
    types = {a["type_key"] for a in r.json()}
    assert types == {"ticket", "hardware"}

    # Only what waits for a human: the register says which states those are.
    r = await client.get("/artifacts?waiting=true", headers=auth(owner))
    waiting = r.json()
    assert [a["ref"] for a in waiting] == ["TST-1"]
    assert waiting[0]["status_label"] == "The plan waits for approval"


async def test_foreign_projects_stay_invisible(client, db, register):
    from app.models.enums import ProjectRole, StatusCategory
    from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    from conftest import add_member

    me = await make_user(db, "ich")
    foreign = await make_project(db, "FRD", "Fremd")
    t = IssueType(project_id=foreign.id, name="Aufgabe")
    s = WorkflowStatus(project_id=foreign.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([t, s, IssueCounter(project_id=foreign.id, last_number=0)])
    await db.commit()
    db.add(Issue(project_id=foreign.id, number=1, key="FRD-1", type_id=t.id, status_id=s.id,
                 summary="Geheim", reporter_id=1, rank="0001"))
    await db.commit()
    await kind.reconcile(db)

    mine = await make_project(db, "MIN", "Meins")
    await add_member(db, mine, me, ProjectRole.owner)
    r = await client.get("/artifacts", headers=auth(me))
    assert [a["title"] for a in r.json()] == []
