"""A name per project.

A radio project knows a callsign, a community project a nickname, and neither of them is the
name on the account. The alias sits on the membership, so it applies exactly where it was
chosen and nowhere else, which is the whole point and therefore what is checked here.
"""
from app.models.enums import ProjectRole
from app.models.project import ProjectMember, member_name
from conftest import add_member, auth, make_project, make_user


def test_the_order_of_the_three_names():
    """Alias before display name before username, and blanks do not count as a name."""
    assert member_name("DL1ABC", "Klaus", "klaus") == "DL1ABC"
    assert member_name("", "Klaus", "klaus") == "Klaus"
    assert member_name("", "", "klaus") == "klaus"
    assert member_name("   ", "  ", "klaus") == "klaus"


async def test_a_member_names_themselves_in_one_project(db, client):
    boss = await make_user(db, "klaus")
    boss.display_name = "Klaus"
    radio = await make_project(db, "ABC", "Radio")
    other = await make_project(db, "XYZ", "Community", inherit_members=False)
    await add_member(db, radio, boss, ProjectRole.owner)
    await add_member(db, other, boss, ProjectRole.owner)
    await db.commit()
    rid, oid, as_boss = radio.id, other.id, auth(boss)

    r = await client.put(f"/projects/{rid}/me/alias", headers=as_boss, json={"alias": "DL1ABC"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "DL1ABC" and r.json()["alias"] == "DL1ABC"

    # Exactly there and nowhere else.
    here = await client.get(f"/projects/{rid}/members", headers=as_boss)
    assert [m["display_name"] for m in here.json()] == ["DL1ABC"]
    elsewhere = await client.get(f"/projects/{oid}/members", headers=as_boss)
    assert [m["display_name"] for m in elsewhere.json()] == ["Klaus"]


async def test_an_empty_alias_puts_the_account_name_back(db, client):
    boss = await make_user(db, "klaus")
    boss.display_name = "Klaus"
    proj = await make_project(db, "ABC", "Radio")
    await add_member(db, proj, boss, ProjectRole.owner)
    await db.commit()
    pid, as_boss = proj.id, auth(boss)

    await client.put(f"/projects/{pid}/me/alias", headers=as_boss, json={"alias": "DL1ABC"})
    r = await client.put(f"/projects/{pid}/me/alias", headers=as_boss, json={"alias": "  "})
    assert r.json()["display_name"] == "Klaus" and r.json()["alias"] == ""


async def test_the_meta_carries_the_name_of_the_project(db, client):
    """Everything that draws a name reads the meta, so the alias has to arrive there."""
    boss = await make_user(db, "klaus")
    boss.display_name = "Klaus"
    proj = await make_project(db, "ABC", "Radio")
    await add_member(db, proj, boss, ProjectRole.owner)
    await db.commit()
    pid, as_boss = proj.id, auth(boss)

    await client.put(f"/projects/{pid}/me/alias", headers=as_boss, json={"alias": "DL1ABC"})
    meta = await client.get(f"/projects/{pid}/meta", headers=as_boss)
    assert [m["display_name"] for m in meta.json()["members"]] == ["DL1ABC"]


async def test_without_a_membership_there_is_nothing_to_name(db, client):
    """An admin sees every project, but a name belongs to a membership."""
    chief = await make_user(db, "chief", admin=True)
    proj = await make_project(db, "ABC", "Radio")
    await db.commit()
    pid = proj.id

    r = await client.put(f"/projects/{pid}/me/alias", headers=auth(chief), json={"alias": "X"})
    assert r.status_code == 404
    assert r.json()["key"] == "err.no_membership_of_this_project"


async def test_a_comment_is_written_under_the_name_of_the_project(db, client, helpers):
    """The ticket history says who did something, and that is the name they carry here."""
    from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    from app.models.enums import StatusCategory

    boss = await make_user(db, "klaus")
    boss.display_name = "Klaus"
    proj = await make_project(db, "ABC", "Radio")
    await add_member(db, proj, boss, ProjectRole.owner)
    t = IssueType(project_id=proj.id, name="Task")
    st = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([t, st, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    await db.refresh(t); await db.refresh(st)
    iss = Issue(project_id=proj.id, number=1, key="ABC-1", type_id=t.id, status_id=st.id,
                summary="Something", reporter_id=boss.id, rank="000000000001")
    db.add(iss)
    await db.commit()
    pid, as_boss = proj.id, auth(boss)

    await client.put(f"/projects/{pid}/me/alias", headers=as_boss, json={"alias": "DL1ABC"})
    r = await client.post("/issues/ABC-1/comments", headers=as_boss,
                          json={"body": "hello", "kind": "internal"})
    assert r.status_code in (200, 201)
    assert r.json()["author_label"] == "DL1ABC"
