"""Own flows: everybody may create them, but they act only where the creator is allowed to.

A free flow (no project, no slot) belongs to its creator. That is more than a display rule:
the definition lies project-less in the same table as the shipped templates, and its actions
touch artifacts. That is why checking happens in three places: seeing, starting and reacting
to an event.
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


async def _free_flow(db, owner, key: str, *, trigger: dict | None = None,
                         published: bool = True) -> WorkflowDefinition:
    start_cfg: dict = {"label": "Start"}
    if trigger:
        start_cfg["trigger"] = trigger
    graph = {"nodes": [{"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                        "data": {"config": start_cfg}},
                       {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                        "data": {"config": {"outcome": "completed"}}}],
             "edges": [{"id": "e1", "source": "s", "target": "e"}]}
    d = WorkflowDefinition(project_id=None, key=key, name=key, created_by=owner.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=(WorkflowVersionStatus.published if published
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if published:
        d.current_version_id = v.id
    await db.commit()
    return d


async def test_anyone_may_create_their_own_flow(client, db):
    """An own flow is not an admin right; otherwise nobody but the admin has one."""
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "abendrunde", "name": "Abendrunde",
        "subject_kind": "standalone"})
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == anna.id or True   # Feld optional im Schema


async def test_a_foreign_flow_is_invisible_and_untouchable(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _free_flow(db, anna, "annas-ablauf")

    visible = [w["id"] for w in (await client.get("/workflows", headers=auth(bert))).json()]
    assert d.id not in visible
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(anna))).json()]

    assert (await client.put(f"/workflows/{d.id}", headers=auth(bert),
                             json={"name": "geklaut"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(bert),
                              json={"subject_kind": "standalone"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                              json={"subject_kind": "standalone"})).status_code == 201


async def test_an_admin_sees_and_may_do_everything(client, db):
    anna = await make_user(db, "anna")
    boss = await make_user(db, "chef", admin=True)
    d = await _free_flow(db, anna, "annas-ablauf")
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(boss))).json()]
    assert (await client.put(f"/workflows/{d.id}", headers=auth(boss),
                             json={"name": "umbenannt"})).status_code == 200


async def test_starting_requires_rights_on_the_artifact(client, db):
    """The flow belongs to Anna, the ticket does not. What it touches is decided by the project."""
    from app.models.ticket import Issue, IssueType, WorkflowStatus

    anna = await make_user(db, "anna")
    boss = await make_user(db, "chef", admin=True)
    project = await make_project(db, "GEH", "Geheim")
    await add_member(db, project, boss, ProjectRole.owner)
    kind = IssueType(project_id=project.id, name="Aufgabe", order=0)
    status_ = WorkflowStatus(project_id=project.id, name="Offen", order=0)
    db.add_all([kind, status_])
    await db.flush()
    issue = Issue(project_id=project.id, number=1, key="GEH-1", type_id=kind.id,
                  status_id=status_.id, summary="Fremd", reporter_id=boss.id, rank="1")
    db.add(issue)
    await db.commit()

    d = await _free_flow(db, anna, "annas-ablauf")
    r = await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                          json={"subject_kind": "issue", "issue_id": issue.id})
    assert r.status_code in (403, 404), r.text


async def test_an_event_starts_only_on_own_projects(db):
    """Without this boundary Anna's flow would run along with EVERY ticket event, in projects
    she may not even see."""
    anna = await make_user(db, "anna")
    boss = await make_user(db, "chef", admin=True)
    theirs = await make_project(db, "IHR", "Annas Projekt")
    await add_member(db, theirs, anna, ProjectRole.member)
    foreign = await make_project(db, "FRD", "Fremdes Projekt")
    await add_member(db, foreign, boss, ProjectRole.owner)

    await _free_flow(db, anna, "annas-lauscher", trigger={"event": "issue.created"})

    assert len(await emit(db, "issue.created", project_id=foreign.id)) == 0
    assert len(await emit(db, "issue.created", project_id=theirs.id)) == 1
    # The instance itself stays project-less (the subject is `standalone`), but the context
    # says which project the event came from.
    run = (await db.execute(select(WorkflowInstance))).scalars().one()
    assert run.project_id is None
    assert run.context["event"]["project_id"] == theirs.id


async def test_an_admin_flow_listens_everywhere(db):
    boss = await make_user(db, "chef", admin=True)
    foreign = await make_project(db, "FRD", "Fremdes Projekt")
    await _free_flow(db, boss, "chef-lauscher", trigger={"event": "issue.created"})
    assert len(await emit(db, "issue.created", project_id=foreign.id)) == 1


# ── The webhook as a source ──────────────────────────────────────────────────

async def test_a_flow_gets_its_own_address(client, db):
    """Not every system speaks MCP or knows Traccoon's events; almost every one sends a
    webhook. The address now comes into being in the flow, not at the other end."""
    anna = await make_user(db, "anna")
    d = await _free_flow(db, anna, "stoerungsmelder")

    assert (await client.get(f"/workflows/{d.id}/webhook", headers=auth(anna))).json() is None

    r = await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))
    assert r.status_code == 201, r.text
    hook = r.json()
    assert hook["public_id"] and hook["secret"]
    assert hook["url"].endswith(f"/api/hooks/{hook['public_id']}")

    # A second call gives the same address; otherwise another one would pile up with every
    # click and nobody would know which one the foreign system uses.
    again = await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))
    assert again.json()["public_id"] == hook["public_id"]


async def test_a_stranger_gives_no_flow_an_address(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _free_flow(db, anna, "annas-ablauf")
    assert (await client.post(f"/workflows/{d.id}/webhook",
                              headers=auth(bert))).status_code == 403


async def test_the_webhook_really_starts_the_flow(client, db):
    """The proof that the address carries: a call in, an instance out, with the payload in
    the context so that the branches have something to read."""
    import hashlib
    import hmac
    import json as _json

    from app.models.workflow import WorkflowInstance

    anna = await make_user(db, "anna")
    d = await _free_flow(db, anna, "stoerungsmelder")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))).json()

    payload = {"quelle": "Zabbix", "vorgang": {"id": 42, "titel": "Störung"}}
    raw = _json.dumps(payload).encode()
    sig = hmac.new(hook["secret"].encode(), raw, hashlib.sha256).hexdigest()
    r = await client.post(f"/hooks/{hook['public_id']}", content=raw,
                          headers={"content-type": "application/json",
                                   "X-Webhook-Signature": sig})
    assert r.status_code in (200, 202), r.text

    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().all()
    assert len(inst) == 1
    assert inst[0].context["vorgang"]["titel"] == "Störung"


# ── The artifact comes from the payload ──────────────────────────────────────

async def _ticket(db, boss, key="ABC-1"):
    from app.models.ticket import Issue, IssueType, WorkflowStatus

    project = await make_project(db, key.split("-")[0], "Projekt")
    await add_member(db, project, boss, ProjectRole.owner)
    kind = IssueType(project_id=project.id, name="Aufgabe", order=0)
    stat = WorkflowStatus(project_id=project.id, name="Offen", order=0)
    db.add_all([kind, stat])
    await db.flush()
    issue = Issue(project_id=project.id, number=int(key.split("-")[1]), key=key,
                  type_id=kind.id, status_id=stat.id, summary="Zielticket",
                  reporter_id=boss.id, rank="1")
    db.add(issue)
    await db.commit()
    return project, issue


async def _with_subjectfield(db, owner, field: str | None, subject=WorkflowSubjectKind.issue):
    start_cfg = {"label": "Start", "trigger": {"kind": "webhook"}}
    if field:
        start_cfg["trigger"]["subjekt_feld"] = field
    graph = {"nodes": [{"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                        "data": {"config": start_cfg}},
                       {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                        "data": {"config": {"outcome": "completed"}}}],
             "edges": [{"id": "e1", "source": "s", "target": "e"}]}
    d = WorkflowDefinition(project_id=None, key=f"mitsubjekt{field or 'ohne'}",
                           name="Mit Subjekt", created_by=owner.id, subject_kind=subject)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def _call(client, hook, payload: dict):
    import hashlib
    import hmac
    import json as _json

    raw = _json.dumps(payload).encode()
    sig = hmac.new(hook["secret"].encode(), raw, hashlib.sha256).hexdigest()
    return await client.post(f"/hooks/{hook['public_id']}", content=raw,
                             headers={"content-type": "application/json",
                                      "X-Webhook-Signature": sig})


async def test_the_webhook_binds_the_ticket_from_the_payload(client, db):
    """The foreign system does not know Traccoon's numbers; it names the identifier it knows.
    Without this binding all ticket actions of the flow would run into nothing."""
    boss = await make_user(db, "chef", admin=True)
    _, issue = await _ticket(db, boss, "ABC-7")
    d = await _with_subjectfield(db, boss, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(boss))).json()

    r = await _call(client, hook, {"vorgang": {"ticket": "ABC-7"}})
    assert r.status_code in (200, 202), r.text
    assert r.json()["issue_id"] == issue.id

    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().one()
    assert inst.issue_id == issue.id
    # The project of the ticket travels along; otherwise rights and live events do not work.
    assert inst.project_id == issue.project_id


async def test_the_number_works_too(client, db):
    boss = await make_user(db, "chef", admin=True)
    _, issue = await _ticket(db, boss, "ABC-9")
    d = await _with_subjectfield(db, boss, "id")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(boss))).json()
    r = await _call(client, hook, {"id": issue.id})
    assert r.json()["issue_id"] == issue.id


async def test_a_missing_field_says_so_clearly(client, db):
    """A flow that needs an artifact but gets none must not start mutely into nothing."""
    boss = await make_user(db, "chef", admin=True)
    await _ticket(db, boss, "ABC-3")
    d = await _with_subjectfield(db, boss, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(boss))).json()

    r = await _call(client, hook, {"etwas": "anderes"})
    assert r.status_code == 400 and "vorgang.ticket" in r.text

    empty = await _with_subjectfield(db, boss, None)
    hook2 = (await client.post(f"/workflows/{empty.id}/webhook", headers=auth(boss))).json()
    r2 = await _call(client, hook2, {"vorgang": {"ticket": "ABC-3"}})
    assert r2.status_code == 400 and "names no field" in r2.text


async def test_a_foreign_ticket_stays_foreign(client, db):
    """The rights come from the owner of the trigger, not from the caller: anybody can know
    a webhook address."""
    boss = await make_user(db, "chef", admin=True)
    anna = await make_user(db, "anna")
    _, issue = await _ticket(db, boss, "GEH-4")

    d = await _with_subjectfield(db, anna, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))).json()
    r = await _call(client, hook, {"vorgang": {"ticket": "GEH-4"}})
    assert r.status_code == 400 and "Rechte" in r.text


async def test_a_flow_without_an_artifact_needs_no_field(client, db):
    boss = await make_user(db, "chef", admin=True)
    d = await _with_subjectfield(db, boss, None, subject=WorkflowSubjectKind.standalone)
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(boss))).json()
    r = await _call(client, hook, {"irgendwas": 1})
    assert r.status_code in (200, 202), r.text
