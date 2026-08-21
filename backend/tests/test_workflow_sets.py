"""Process sets: the resolution chain, adjusting (copy-on-write) and resetting.

The core of the promise to the user: "projects get a default set that can be reset at any
time; an own set applies to all projects in which I am the owner".
"""
from app.models.enums import ProjectRole, WorkflowSlot
from app.models.workflow import WorkflowDefinition, WorkflowInstance
from app.services import workflow_sets as sets
from conftest import add_member, auth, make_project, make_user

LIFECYCLE = WorkflowSlot.ticket_lifecycle.value


async def test_the_default_preset_applies_without_action(db, seeded):
    proj = await make_project(db, "TST", "Test")
    d = await sets.resolve_definition(db, proj.id, LIFECYCLE)
    assert d is not None and d.set_id == seeded.id
    info = await sets.resolve_source(db, proj.id, LIFECYCLE)
    assert info["origin"] == "builtin"


async def test_an_own_preset_applies_to_my_owned_projects(db, seeded):
    owner = await make_user(db, "owner")
    fremd = await make_user(db, "fremd")
    meins = await make_project(db, "MEIN", "Meins")
    anderes = await make_project(db, "AND", "Anderes")
    await add_member(db, meins, owner, ProjectRole.owner)
    await add_member(db, anderes, fremd, ProjectRole.owner)
    # Only a member (not the owner), so my set does NOT apply here.
    await add_member(db, anderes, owner, ProjectRole.member)

    eigener = await sets.create_user_set(db, owner, "Meine Prozesse")

    meine = await sets.resolve_source(db, meins.id, LIFECYCLE)
    assert meine["set"].id == eigener.id and meine["origin"] == "user"
    assert (await sets.resolve_source(db, anderes.id, LIFECYCLE))["origin"] == "builtin"


async def test_adjusting_detaches_and_resetting_binds_again(db, seeded):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)

    kopie = await sets.customize(db, proj, LIFECYCLE, owner.id)
    assert kopie.project_id == proj.id and kopie.set_id is None
    info = await sets.resolve_source(db, proj.id, LIFECYCLE)
    assert info["origin"] == "project" and info["definition"].id == kopie.id

    # Adjusting twice creates no second copy.
    assert (await sets.customize(db, proj, LIFECYCLE, owner.id)).id == kopie.id

    assert await sets.reset(db, proj, LIFECYCLE) is True
    assert (await sets.resolve_source(db, proj.id, LIFECYCLE))["origin"] == "builtin"
    # The copy is kept as an archive (history, running instances).
    await db.refresh(kopie)
    assert kopie.archived_at is not None


async def test_resetting_leaves_a_running_instance_untouched(db, seeded, client):
    """A running instance hangs off its version: resetting must not topple it."""
    from app.models.enums import WorkflowInstanceStatus, WorkflowSubjectKind
    from app.services.workflow_engine import start_workflow

    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)
    kopie = await sets.customize(db, proj, WorkflowSlot.ticket_intake.value, owner.id)

    inst = await start_workflow(db, kopie, subject_kind=WorkflowSubjectKind.standalone,
                                context={"ignore": True}, actor_id=owner.id, advance_now=False)
    await sets.reset(db, proj, WorkflowSlot.ticket_intake.value)

    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.running
    assert inst.version_id == kopie.current_version_id
    assert await db.get(WorkflowDefinition, kopie.id) is not None


async def test_the_slot_overview_shows_the_origin(client, db, seeded):
    owner = await make_user(db, "owner", admin=True)
    proj = await make_project(db, "TST", "Test")
    m = await add_member(db, proj, owner, ProjectRole.owner)
    m.ai_assign = True
    await db.commit()

    r = await client.get(f"/projects/{proj.id}/workflow-slots", headers=auth(owner))
    assert r.status_code == 200, r.text
    slots = {s["slot"]: s for s in r.json()}
    assert set(slots) == {s.value for s in WorkflowSlot}
    assert slots[LIFECYCLE]["origin"] == "builtin"
    assert slots[LIFECYCLE]["published"] is True

    r = await client.post(f"/projects/{proj.id}/workflow-slots/{LIFECYCLE}/customize",
                          headers=auth(owner))
    assert r.status_code == 201, r.text
    r = await client.get(f"/projects/{proj.id}/workflow-slots", headers=auth(owner))
    assert {s["slot"]: s for s in r.json()}[LIFECYCLE]["origin"] == "project"

    r = await client.post(f"/projects/{proj.id}/workflow-slots/{LIFECYCLE}/reset",
                          headers=auth(owner))
    assert r.status_code == 200 and r.json() == {"reset": True}
    r = await client.get(f"/projects/{proj.id}/workflow-slots", headers=auth(owner))
    assert {s["slot"]: s for s in r.json()}[LIFECYCLE]["origin"] == "builtin"


async def test_seeding_is_idempotent(db, seeded):
    from app.services.workflow_seed import ensure_builtin_set
    from sqlalchemy import func, select
    from app.models.workflow import WorkflowVersion

    vorher = (await db.execute(select(func.count()).select_from(WorkflowVersion))).scalar()
    await ensure_builtin_set(db)
    await ensure_builtin_set(db)
    assert (await db.execute(select(func.count()).select_from(WorkflowVersion))).scalar() == vorher


async def test_another_persons_preset_is_off_limits(client, db, seeded):
    a = await make_user(db, "anna")
    b = await make_user(db, "bert")
    preset = await sets.create_user_set(db, a)
    from sqlalchemy import select
    d = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.set_id == preset.id))).scalars().first()

    r = await client.put(f"/workflows/{d.id}", json={"name": "geklaut"}, headers=auth(b))
    assert r.status_code == 403


async def test_the_instance_knows_the_project_even_with_a_preset_template(db, seeded):
    """Templates are project-less; the instance still has to hang off the project, because
    otherwise the rights check and the live events do not work."""
    from app.models.enums import TicketAgentStatus
    from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    from app.models.enums import StatusCategory
    from app.services.lifecycle_flow import start_lifecycle

    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)
    t = IssueType(project_id=proj.id, name="Aufgabe")
    s = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([t, s, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key="TST-1", type_id=t.id, status_id=s.id,
                  summary="Test", reporter_id=owner.id, rank="0001",
                  assigned_agent="developer", assigned_by_user_id=owner.id,
                  agent_status=TicketAgentStatus.planning)
    db.add(issue)
    await db.commit()

    inst = await start_lifecycle(db, issue, owner.id, advance_now=False)
    assert isinstance(inst, WorkflowInstance)
    assert inst.project_id == proj.id
    await db.refresh(issue)
    assert issue.workflow_instance_id == inst.id
