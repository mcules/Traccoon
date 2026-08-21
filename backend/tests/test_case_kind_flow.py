"""The issue type chooses the process.

Until now every ticket of a project ran the same lifecycle. Now a bug may have one of its
own while task and requirement keep following the set; the copy hangs off the issue type for
that.

Resolution: issue type, then project-owned (generic), then set, then owner set, then default.
"""
from app.models.enums import ProjectRole, StatusCategory
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import workflow_sets as sets
from app.services.workflow_seed import ensure_builtin_set
from conftest import add_member, auth, make_project, make_user
from sqlalchemy import select

SLOT = "ticket_lifecycle"


async def _projekt_mit_kinds(db, key="VGA"):
    proj = await make_project(db, key, "Vorgangsarten")
    aufgabe = IssueType(project_id=proj.id, name="Aufgabe", order=0)
    bug = IssueType(project_id=proj.id, name="Bug", order=1)
    db.add_all([aufgabe, bug, IssueCounter(project_id=proj.id, last_number=0),
                WorkflowStatus(project_id=proj.id, name="To Do",
                               category=StatusCategory.todo, order=0)])
    await db.commit()
    return proj, aufgabe, bug


async def test_without_an_own_copy_the_same_applies_to_all(db):
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_kinds(db)

    fuer_aufgabe = await sets.resolve_definition(db, proj.id, SLOT, aufgabe.id)
    fuer_bug = await sets.resolve_definition(db, proj.id, SLOT, bug.id)
    assert fuer_aufgabe is not None
    assert fuer_aufgabe.id == fuer_bug.id      # both follow the default


async def test_a_copy_for_one_case_kind_applies_only_there(db):
    """The core: an own flow for bugs leaves all the others untouched."""
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_kinds(db)
    standard = await sets.resolve_definition(db, proj.id, SLOT)

    eigen = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)
    assert eigen.issue_type_id == bug.id

    assert (await sets.resolve_definition(db, proj.id, SLOT, bug.id)).id == eigen.id
    assert (await sets.resolve_definition(db, proj.id, SLOT, aufgabe.id)).id == standard.id
    # And without naming the issue type it stays with the default.
    assert (await sets.resolve_definition(db, proj.id, SLOT)).id == standard.id


async def test_a_general_copy_applies_where_no_specific_one_stands(db):
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_kinds(db)
    allgemein = await sets.customize(db, proj, SLOT, actor_id=None)
    fuer_bug = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)

    assert (await sets.resolve_definition(db, proj.id, SLOT, bug.id)).id == fuer_bug.id
    assert (await sets.resolve_definition(db, proj.id, SLOT, aufgabe.id)).id == allgemein.id


async def test_adjusting_twice_yields_the_same_copy(db):
    """Otherwise silent duplicates would arise; the index forbids them anyway."""
    await ensure_builtin_set(db)
    proj, _, bug = await _projekt_mit_kinds(db)
    a = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)
    b = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)
    assert a.id == b.id


async def test_the_lifecycle_starts_the_flow_of_the_case_kind(db):
    """Not only the resolution: the real start has to take the issue type into account."""
    from app.services.lifecycle_flow import start_lifecycle
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_kinds(db)
    eigen = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)

    spalte = (await db.execute(select(WorkflowStatus).where(
        WorkflowStatus.project_id == proj.id))).scalars().first()
    ticket = Issue(project_id=proj.id, number=1, key="VGA-1", type_id=bug.id,
                   status_id=spalte.id, summary="Ein Bug", reporter_id=1, rank="0001",
                   assigned_agent="dev")
    db.add(ticket)
    await db.commit()

    inst = await start_lifecycle(db, ticket, None, entry="plan", advance_now=False)
    await db.commit()
    assert inst is not None and inst.definition_id == eigen.id


async def test_the_api_creates_a_copy_per_case_kind(client, db):
    await ensure_builtin_set(db)
    chef = await make_user(db, "chef", admin=True)
    proj, aufgabe, bug = await _projekt_mit_kinds(db)
    await add_member(db, proj, chef, ProjectRole.owner)
    await db.commit()
    pid, bug_id, aufgabe_id = proj.id, bug.id, aufgabe.id

    r = await client.post(
        f"/projects/{pid}/workflow-slots/{SLOT}/customize?issue_type_id={bug_id}",
        headers=auth(chef))
    assert r.status_code == 201, r.text
    assert r.json()["issue_type_id"] == bug_id

    # The task keeps following the set.
    fuer_aufgabe = await sets.resolve_definition(db, pid, SLOT, aufgabe_id)
    assert fuer_aufgabe.id != r.json()["id"]
