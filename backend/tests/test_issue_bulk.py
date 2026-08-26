"""The same handle over several tickets.

The point of this endpoint is not that it is faster. It is that it obeys the same rules as a
single ticket: the one in the selection that may not jump to "done" has to be refused there
as well, and it must not take the others down with it. Both directions are pinned here,
because a bulk action that quietly does more than the drawer would is the worst outcome of
the feature.
"""
from sqlalchemy import select

from app.models.enums import Priority, ProjectRole, TicketAgentStatus
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.models.enums import StatusCategory
from conftest import auth, make_project, make_user, add_member


async def _project(db, *, testenv=True):
    boss = await make_user(db, "chef")
    proj = await make_project(db, "ABC", "A project")
    member = await add_member(db, proj, boss, ProjectRole.owner)
    member.ai_assign = True
    proj.testenv_enabled = testenv
    t = IssueType(project_id=proj.id, name="Task")
    stats = {}
    for i, (name, cat) in enumerate([("To Do", StatusCategory.todo),
                                     ("In progress", StatusCategory.in_progress),
                                     ("Done", StatusCategory.done)]):
        s = WorkflowStatus(project_id=proj.id, name=name, category=cat, order=i)
        db.add(s); stats[name] = s
    db.add_all([t, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    return boss, proj, t, stats


async def _make(db, proj, t, stats, boss, n, **kw):
    iss = Issue(project_id=proj.id, number=n, key=f"ABC-{n}", type_id=t.id,
                status_id=stats[kw.pop("column", "To Do")].id, summary=f"Ticket {n}",
                reporter_id=boss.id, rank=f"{n:012d}", **kw)
    db.add(iss)
    await db.commit()
    await db.refresh(iss)
    return iss


async def test_a_selection_changes_the_status(db, client):
    boss, proj, t, stats = await _project(db)
    a = await _make(db, proj, t, stats, boss, 1)
    b = await _make(db, proj, t, stats, boss, 2)

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [a.key, b.key], "action": "status",
                                "status_id": stats["In progress"].id})
    assert r.status_code == 200
    assert r.json()["done"] == 2 and r.json()["failed"] == []
    for iss in (a, b):
        await db.refresh(iss)
        assert iss.status_id == stats["In progress"].id


async def test_the_ranks_stay_apart(db, client):
    """Everything moved lands at the END of the target column, one after another.

    Without that they would all take the same position, and their order in the column would
    depend on the sequence the loop happened to work through.
    """
    boss, proj, t, stats = await _project(db)
    ids = [await _make(db, proj, t, stats, boss, n) for n in (1, 2, 3)]

    await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                      json={"keys": [i.key for i in ids], "action": "status",
                            "status_id": stats["In progress"].id})
    ranks = []
    for iss in ids:
        await db.refresh(iss)
        ranks.append(iss.rank)
    assert len(set(ranks)) == 3, "every ticket has a place of its own"


async def test_one_refusal_does_not_take_the_others_with_it(db, client):
    """The whole reason this answers instead of throwing.

    A ticket on "testing" may not jump to done, that guard exists so that no unmerged branch
    is shown as finished. In a selection of three it must refuse exactly one.
    """
    boss, proj, t, stats = await _project(db)
    ok1 = await _make(db, proj, t, stats, boss, 1)
    blocked = await _make(db, proj, t, stats, boss, 2, agent_status=TicketAgentStatus.testing)
    ok2 = await _make(db, proj, t, stats, boss, 3)

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [ok1.key, blocked.key, ok2.key], "action": "status",
                                "status_id": stats["Done"].id})
    body = r.json()
    assert body["done"] == 2
    assert [f["key"] for f in body["failed"]] == [blocked.key]
    assert body["failed"][0]["error_key"] == "err.direct_jump_to_done"

    await db.refresh(ok1); await db.refresh(blocked)
    assert ok1.status_id == stats["Done"].id
    assert blocked.status_id == stats["To Do"].id, "the refused one stays where it was"


async def test_priority_and_archive(db, client):
    boss, proj, t, stats = await _project(db)
    a = await _make(db, proj, t, stats, boss, 1)
    b = await _make(db, proj, t, stats, boss, 2)
    keys = [a.key, b.key]

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": keys, "action": "priority", "priority": "high"})
    assert r.json()["done"] == 2
    await db.refresh(a)
    assert a.priority == Priority.high

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": keys, "action": "archive"})
    assert r.json()["done"] == 2
    await db.refresh(a)
    assert a.archived is True

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": keys, "action": "unarchive"})
    assert r.json()["done"] == 2
    await db.refresh(a)
    assert a.archived is False


async def test_deleting_needs_maintainer(db, client):
    """A member may write, not delete. The same line as at a single ticket."""
    boss, proj, t, stats = await _project(db)
    hand = await make_user(db, "hand")
    await add_member(db, proj, hand, ProjectRole.member)
    a = await _make(db, proj, t, stats, boss, 1)
    # The ids BEFORE the first request: a refusal rolls the shared session back, and every
    # object in it is expired afterwards. Reading `proj.id` off it would then load lazily,
    # outside the async context, which is a MissingGreenlet and not the fault under test.
    pid, aid, akey = proj.id, a.id, a.key
    as_hand, as_boss = auth(hand), auth(boss)

    r = await client.post(f"/projects/{pid}/issues/bulk", headers=as_hand,
                          json={"keys": [akey], "action": "delete"})
    assert r.json()["done"] == 0
    assert r.json()["failed"][0]["error_key"] == "err.deleting_requires_maintainer"
    assert (await db.get(Issue, aid)) is not None

    r = await client.post(f"/projects/{pid}/issues/bulk", headers=as_boss,
                          json={"keys": [akey], "action": "delete"})
    assert r.json()["done"] == 1
    assert (await db.get(Issue, aid)) is None


async def test_a_foreign_key_is_reported_not_thrown(db, client):
    """A stale list is the normal case: somebody deleted the ticket while the ticks stood."""
    boss, proj, t, stats = await _project(db)
    a = await _make(db, proj, t, stats, boss, 1)

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [a.key, "ABC-999"], "action": "priority",
                                "priority": "low"})
    body = r.json()
    assert body["done"] == 1
    assert body["failed"][0]["key"] == "ABC-999"
    assert body["failed"][0]["error_key"] == "err.ticket_not_found"


async def test_an_empty_selection_does_nothing(db, client):
    boss, proj, t, stats = await _project(db)
    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [], "action": "archive"})
    assert r.json() == {"done": 0, "failed": []}


async def test_too_many_at_once_is_refused(db, client):
    """A cap, so that one request cannot occupy the database for minutes."""
    boss, proj, t, stats = await _project(db)
    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [f"ABC-{n}" for n in range(300)], "action": "archive"})
    assert r.status_code == 400
    assert r.json()["key"] == "err.too_many_tickets_at_once"


async def test_a_selection_moves_into_a_sprint_and_back(db, client):
    """The backlog moves tickets the same way as everything else: over the selection."""
    from app.models.ticket import Board, Sprint

    boss, proj, t, stats = await _project(db)
    board = Board(project_id=proj.id, name="Board")
    db.add(board)
    await db.commit()
    await db.refresh(board)
    sprint = Sprint(board_id=board.id, name="Sprint 1")
    db.add(sprint)
    await db.commit()
    await db.refresh(sprint)
    a = await _make(db, proj, t, stats, boss, 1)
    b = await _make(db, proj, t, stats, boss, 2)

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [a.key, b.key], "action": "sprint",
                                "sprint_id": sprint.id})
    assert r.json()["done"] == 2
    await db.refresh(a)
    assert a.sprint_id == sprint.id

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [a.key], "action": "sprint", "sprint_id": None})
    assert r.json()["done"] == 1
    await db.refresh(a)
    assert a.sprint_id is None


async def test_a_foreign_sprint_is_refused(db, client):
    """A sprint hangs off a board and that off a project. Without the check a ticket could be
    put on a board whose people never chose to show it."""
    from app.models.ticket import Board, Sprint

    boss, proj, t, stats = await _project(db)
    other = await make_project(db, "XYZ", "Another project")
    board = Board(project_id=other.id, name="Foreign board")
    db.add(board)
    await db.commit()
    await db.refresh(board)
    foreign = Sprint(board_id=board.id, name="Foreign sprint")
    db.add(foreign)
    await db.commit()
    await db.refresh(foreign)
    a = await _make(db, proj, t, stats, boss, 1)

    r = await client.post(f"/projects/{proj.id}/issues/bulk", headers=auth(boss),
                          json={"keys": [a.key], "action": "sprint", "sprint_id": foreign.id})
    body = r.json()
    assert body["done"] == 0
    assert body["failed"][0]["error_key"] == "err.sprint_does_not_belong_project"
    await db.refresh(a)
    assert a.sprint_id is None
