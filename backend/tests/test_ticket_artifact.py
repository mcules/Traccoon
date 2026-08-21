"""Tickets as artifacts, and the reconciliation that covers all write sites.

`agent_status` is set in 21 places in 10 files (endpoints, Telegram bot, PM chat, worker,
process actions). Instead of maintaining each of them, one pass aligns the artifact rows, at
the start and in the 30 second tick of the process engine.
"""
from app.models.artifact import Artifact
from app.models.enums import StatusCategory, TicketAgentStatus, WorkflowSubjectKind
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import artifacts as kind
from sqlalchemy import select
from conftest import make_asset, make_project, make_user
import pytest


async def _ticket(db, proj, summary="Ein Ticket", nummer=1, status=None) -> Issue:
    t = (await db.execute(select(IssueType).where(IssueType.project_id == proj.id))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == proj.id))).scalars().first()
    if t is None:
        t = IssueType(project_id=proj.id, name="Aufgabe")
        s = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
        db.add_all([t, s, IssueCounter(project_id=proj.id, last_number=0)])
        await db.commit()
    i = Issue(project_id=proj.id, number=nummer, key=f"{proj.key}-{nummer}", type_id=t.id,
              status_id=s.id, summary=summary, reporter_id=1, rank=f"{nummer:04d}",
              agent_status=status)
    db.add(i)
    await db.commit()
    return i


@pytest.fixture
async def register(db):
    await kind.ensure_builtin_types(db)


async def test_the_reconcile_creates_missing_rows(db, register):
    proj = await make_project(db, "TST", "Test")
    a = await _ticket(db, proj, "Erstes", 1)
    b = await _ticket(db, proj, "Zweites", 2, TicketAgentStatus.plan_review)

    result = await kind.reconcile(db)
    assert result["tickets_neu"] == 2
    await db.refresh(a); await db.refresh(b)
    assert a.artifact_id and b.artifact_id and a.artifact_id != b.artifact_id

    kind_b = await db.get(Artifact, b.artifact_id)
    assert kind_b.title == "Zweites"
    assert kind_b.status_key == "plan_review"
    assert kind_b.project_id == proj.id


async def test_the_reconcile_catches_up_on_arbitrary_write_points(db, register):
    """The actual purpose: a place sets agent_status directly (as the bot, the PM chat or the
    worker do), and the reconciliation pulls the artifact row along."""
    proj = await make_project(db, "TST", "Test")
    i = await _ticket(db, proj, "Alt", 1)
    await kind.reconcile(db)
    await db.refresh(i)

    i.agent_status = TicketAgentStatus.hold        # directly, without apply_status
    i.summary = "Neu benannt"
    await db.commit()

    result = await kind.reconcile(db)
    assert result["tickets_angeglichen"] == 1
    a = await db.get(Artifact, i.artifact_id)
    await db.refresh(a)
    assert a.status_key == "hold" and a.title == "Neu benannt"


async def test_the_reconcile_is_idempotent(db, register):
    proj = await make_project(db, "TST", "Test")
    await _ticket(db, proj, "Eins", 1)
    await make_asset(db, "Switch", project=proj)
    await kind.reconcile(db)

    zweiter = await kind.reconcile(db)
    assert not any(zweiter.values()), zweiter


async def test_setting_the_state_writes_along_immediately(db, register):
    """The common path does not wait for the reconciliation."""
    proj = await make_project(db, "TST", "Test")
    i = await _ticket(db, proj, "Sofort", 1)
    await kind.reconcile(db)
    await db.refresh(i)

    await kind.apply_status(db, subject_kind=WorkflowSubjectKind.issue, issue=i,
                           status_key="to_test")
    await db.commit()
    a = await db.get(Artifact, i.artifact_id)
    await db.refresh(a)
    assert a.status_key == "to_test"


async def test_hardware_and_ticket_share_the_same_store(db, register):
    proj = await make_project(db, "TST", "Test")
    await _ticket(db, proj, "Ticket", 1)
    asset = await make_asset(db, "Switch", project=proj)
    await kind.ensure_for_asset(db, asset)
    await db.commit()
    await kind.reconcile(db)

    lines = (await db.execute(select(Artifact))).scalars().all()
    typen = {}
    for z in lines:
        t = await db.get(type(await kind.type_by_key(db, "ticket")), z.type_id)
        typen[t.key] = typen.get(t.key, 0) + 1
    assert typen == {"ticket": 1, "hardware": 1}
