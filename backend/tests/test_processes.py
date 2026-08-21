"""Process administration: the default set with deviations, operation, triggers, rolling back.

The four questions an administration has to answer, and the boundary that must never fall in
the process: a user sees only projects they have access to.
"""
import datetime as dt
import itertools

import pytest
from app.models.enums import (
    ProjectRole, WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.workflow import (
    WorkflowDefinition, WorkflowInstance, WorkflowSet, WorkflowVersion,
)
from app.services import workflow_sets as sets
from app.services.workflow_seed import ensure_builtin_set
from conftest import add_member, auth, make_project, make_user
from sqlalchemy import select


@pytest.fixture
async def standard(db):
    """Create the shipped set: the basis of all deviation checks."""
    await ensure_builtin_set(db)
    return (await db.execute(select(WorkflowSet).where(
        WorkflowSet.key == sets.BUILTIN_SET_KEY))).scalars().first()


async def test_slots_show_the_shipped_set(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/slots", headers=auth(admin))
    assert r.status_code == 200, r.text
    data = {s["slot"]: s for s in r.json()}
    assert set(data) == set(sets.SLOT_META)
    lz = data["ticket_lifecycle"]
    assert lz["published"] is True
    assert lz["version"] >= 1
    assert lz["deviations"] == []


async def test_a_project_copy_appears_as_a_deviation(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "ABW", "Abweichler")
    await add_member(db, proj, admin, ProjectRole.owner)
    await sets.customize(db, proj, "ticket_lifecycle", admin.id)
    await db.commit()

    r = await client.get("/processes/slots", headers=auth(admin))
    lz = next(s for s in r.json() if s["slot"] == "ticket_lifecycle")
    assert [a["project_key"] for a in lz["deviations"]] == ["ABW"]


async def test_foreign_projects_stay_hidden(client, db, standard):
    """A deviation would otherwise reveal names of projects that are none of one's business."""
    owner = await make_user(db, "eigner", admin=True)
    foreign = await make_user(db, "fremd")
    proj = await make_project(db, "GEH", "Geheim", inherit_members=False)
    await add_member(db, proj, owner, ProjectRole.owner)
    await sets.customize(db, proj, "ticket_lifecycle", owner.id)
    await db.commit()

    r = await client.get("/processes/slots", headers=auth(foreign))
    lz = next(s for s in r.json() if s["slot"] == "ticket_lifecycle")
    assert lz["deviations"] == []


# ── Betrieb ──────────────────────────────────────────────────────────────────

_lfd = itertools.count(1)


async def _instance(db, proj, *, status, age_hours=0.0) -> WorkflowInstance:
    # The key is unique per project; one test creates several flows.
    d = WorkflowDefinition(project_id=proj.id, key=f"ab{next(_lfd)}", name="Ablauf",
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [{"id": "s", "type": "start",
                                          "data": {"config": {"label": "Start"}}}], "edges": []})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id, project_id=proj.id,
                            subject_kind=WorkflowSubjectKind.standalone, status=status,
                            context={})
    db.add(inst)
    await db.flush()
    if age_hours:
        inst.started_at = (dt.datetime.now(tz=dt.timezone.utc)
                           - dt.timedelta(hours=age_hours))
    await db.commit()
    return inst


async def test_waiting_cases_belong_in_the_operations_view(client, db):
    """`waiting` is the normal case: if the default view left it out, it would be blind."""
    user = await make_user(db, "op", admin=True)
    proj = await make_project(db, "OPS", "Betrieb")
    await add_member(db, proj, user, ProjectRole.owner)
    await _instance(db, proj, status=WorkflowInstanceStatus.waiting)

    r = await client.get("/processes/running", headers=auth(user))
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()] == ["waiting"]


async def test_finished_work_stays_out_until_asked_for(client, db):
    user = await make_user(db, "op", admin=True)
    proj = await make_project(db, "OPS", "Betrieb")
    await add_member(db, proj, user, ProjectRole.owner)
    await _instance(db, proj, status=WorkflowInstanceStatus.completed)

    assert (await client.get("/processes/running", headers=auth(user))).json() == []
    mit = await client.get("/processes/running?include_done=true", headers=auth(user))
    assert len(mit.json()) == 1


async def test_a_long_wait_counts_as_hanging(client, db):
    user = await make_user(db, "op", admin=True)
    proj = await make_project(db, "OPS", "Betrieb")
    await add_member(db, proj, user, ProjectRole.owner)
    await _instance(db, proj, status=WorkflowInstanceStatus.waiting, age_hours=50)
    await _instance(db, proj, status=WorkflowInstanceStatus.waiting)

    alle = (await client.get("/processes/running", headers=auth(user))).json()
    assert sorted(x["hangs"] for x in alle) == [False, True]
    only = (await client.get("/processes/running?only_stuck=true", headers=auth(user))).json()
    assert len(only) == 1 and only[0]["hangs"] is True


async def test_foreign_cases_are_invisible(client, db):
    owner = await make_user(db, "eigner", admin=True)
    foreign = await make_user(db, "fremd")
    proj = await make_project(db, "GEH", "Geheim", inherit_members=False)
    await add_member(db, proj, owner, ProjectRole.owner)
    await _instance(db, proj, status=WorkflowInstanceStatus.waiting)

    assert (await client.get("/processes/running", headers=auth(foreign))).json() == []


# ── Triggers ─────────────────────────────────────────────────────────────────

async def test_the_trigger_finds_subflows_and_manual_starts(client, db, standard):
    """The acceptance flow is called by the lifecycle; otherwise it would look triggerless."""
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/triggers", headers=auth(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    acceptance = [t for t in data if t["slot"] == "acceptance"]
    assert acceptance and acceptance[0]["kind"] == "subflow"
    assert "KI-Ticket-Lebenszyklus" in acceptance[0]["label"]
    # Every published flow turns up exactly once.
    assert {t["slot"] for t in data} >= set(sets.SLOT_META)


async def test_events_count_their_listeners(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/events", headers=auth(admin))
    assert r.status_code == 200
    data = r.json()
    assert {e["event"] for e in data}
    # Without a set trigger nobody listens, and the overview should show that honestly. The
    # shipped set contains no event driven flow any more: the mail inbox left it and is now
    # a template one creates for oneself.
    listener = {e["event"]: e["listeners"] for e in data}
    assert all(z == 0 for z in listener.values())


# ── Rolling back ─────────────────────────────────────────────────────────────

async def test_rolling_back_creates_a_new_version(client, db, standard):
    """The history stays: rolling back happens by publishing, not by bending a pointer."""
    admin = await make_user(db, "chef", admin=True)
    d = await sets.set_definition(db, standard.id, "ticket_lifecycle")
    first = await db.get(WorkflowVersion, d.current_version_id)
    graph = dict(first.graph)

    second = WorkflowVersion(definition_id=d.id, version=first.version + 1, graph=graph,
                             status=WorkflowVersionStatus.published,
                             published_at=dt.datetime.now(tz=dt.timezone.utc))
    db.add(second)
    await db.flush()
    d.current_version_id = second.id
    await db.commit()

    r = await client.post(f"/workflows/{d.id}/versions/{first.id}/rollback", headers=auth(admin))
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["version"] == second.version + 1
    assert f"{first.version}" in new["notes"]
    await db.refresh(d)
    assert d.current_version_id == new["id"]
    # The old version is untouched: running instances hang off it.
    await db.refresh(first)
    assert first.status == WorkflowVersionStatus.published


async def test_rolling_back_to_the_current_version_is_a_conflict(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    d = await sets.set_definition(db, standard.id, "ticket_lifecycle")
    r = await client.post(f"/workflows/{d.id}/versions/{d.current_version_id}/rollback",
                          headers=auth(admin))
    assert r.status_code == 409


async def test_only_an_admin_may_roll_back_the_default(client, db, standard):
    nobody = await make_user(db, "gast")
    d = await sets.set_definition(db, standard.id, "ticket_lifecycle")
    first = await db.get(WorkflowVersion, d.current_version_id)
    r = await client.post(f"/workflows/{d.id}/versions/{first.id}/rollback", headers=auth(nobody))
    assert r.status_code == 403


async def test_context_fields_name_their_origin(client, db):
    """The editor needs the fields a flow really has; guessing went on long enough (a free
    text field, and a typo only stood out in operation)."""
    anna = await make_user(db, "anna")
    r = await client.get("/workflow-context-fields", headers=auth(anna))
    assert r.status_code == 200, r.text
    k = r.json()
    assert {"base", "triggers", "actions", "nodes"} <= set(k)

    # A trigger brings its payload along …
    mail = [f["path"] for f in k["triggers"]["mail.received"]]
    assert "mail.subject" in mail and "intake.owner_id" in mail
    # … an action its results …
    assert "spam.score" in [f["path"] for f in k["actions"]["spam_evaluate"]]
    assert "project.needs_acceptance" in [f["path"] for f in k["actions"]["refresh_facts"]]
    # … and an agent run what the lifecycle branches on.
    assert "agent.has_subtickets" in [f["path"] for f in k["nodes"]["agent_task"]]
    # Every field explains itself.
    assert all(f["description"] and f["type"] for f in k["base"])


async def test_context_fields_cover_the_guards_of_the_default_set(client, db):
    """What the shipped flows read at their branches has to stand in the catalog; otherwise it
    describes something other than what runs."""
    from app.services.workflow_seed import BUILDERS

    anna = await make_user(db, "anna")
    k = (await client.get("/workflow-context-fields", headers=auth(anna))).json()
    known = {f["path"] for group in ("base",) for f in k[group]}
    for pot in ("triggers", "actions", "nodes"):
        for fields in k[pot].values():
            known |= {f["path"] for f in fields}

    def vars_from(rule, out: set):
        if isinstance(rule, dict):
            for op, args in rule.items():
                if op == "var" and isinstance(args, str):
                    out.add(args)
                else:
                    vars_from(args, out)
        elif isinstance(rule, list):
            for a in rule:
                vars_from(a, out)

    used: set[str] = set()
    for build in BUILDERS.values():
        for n in build()["nodes"]:
            cfg = (n.get("data") or {}).get("config") or {}
            for b in cfg.get("branches") or []:
                vars_from(b.get("guard"), used)

    # `entry` controls the entry of the lifecycle and comes from the caller, not from an
    # action; the rest has to stand in the catalog.
    missing = {v for v in used if v not in known and v != "entry"}
    assert not missing, f"Guards read fields the catalog does not know: {sorted(missing)}"
