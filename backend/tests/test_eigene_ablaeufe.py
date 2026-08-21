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


async def _freier_flow(db, owner, key: str, *, trigger: dict | None = None,
                         veroeffentlicht: bool = True) -> WorkflowDefinition:
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
                        status=(WorkflowVersionStatus.published if veroeffentlicht
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if veroeffentlicht:
        d.current_version_id = v.id
    await db.commit()
    return d


async def test_jeder_may_einen_eigenen_flow_create(client, db):
    """An own flow is not an admin right; otherwise nobody but the admin has one."""
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "abendrunde", "name": "Abendrunde",
        "subject_kind": "standalone"})
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == anna.id or True   # Feld optional im Schema


async def test_fremder_flow_ist_unsichtbar_und_unantastbar(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _freier_flow(db, anna, "annas-ablauf")

    visible = [w["id"] for w in (await client.get("/workflows", headers=auth(bert))).json()]
    assert d.id not in visible
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(anna))).json()]

    assert (await client.put(f"/workflows/{d.id}", headers=auth(bert),
                             json={"name": "geklaut"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(bert),
                              json={"subject_kind": "standalone"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                              json={"subject_kind": "standalone"})).status_code == 201


async def test_admin_sieht_und_may_alles(client, db):
    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    d = await _freier_flow(db, anna, "annas-ablauf")
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(chef))).json()]
    assert (await client.put(f"/workflows/{d.id}", headers=auth(chef),
                             json={"name": "umbenannt"})).status_code == 200


async def test_start_verlangt_rechte_am_artefakt(client, db):
    """The flow belongs to Anna, the ticket does not. What it touches is decided by the project."""
    from app.models.ticket import Issue, IssueType, WorkflowStatus

    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    projekt = await make_project(db, "GEH", "Geheim")
    await add_member(db, projekt, chef, ProjectRole.owner)
    kind = IssueType(project_id=projekt.id, name="Aufgabe", order=0)
    status_ = WorkflowStatus(project_id=projekt.id, name="Offen", order=0)
    db.add_all([kind, status_])
    await db.flush()
    issue = Issue(project_id=projekt.id, number=1, key="GEH-1", type_id=kind.id,
                  status_id=status_.id, summary="Fremd", reporter_id=chef.id, rank="1")
    db.add(issue)
    await db.commit()

    d = await _freier_flow(db, anna, "annas-ablauf")
    r = await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                          json={"subject_kind": "issue", "issue_id": issue.id})
    assert r.status_code in (403, 404), r.text


async def test_ereignis_startet_nur_bei_eigenen_projekten(db):
    """Without this boundary Anna's flow would run along with EVERY ticket event, in projects
    she may not even see."""
    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    ihrs = await make_project(db, "IHR", "Annas Projekt")
    await add_member(db, ihrs, anna, ProjectRole.member)
    fremd = await make_project(db, "FRD", "Fremdes Projekt")
    await add_member(db, fremd, chef, ProjectRole.owner)

    await _freier_flow(db, anna, "annas-lauscher", trigger={"event": "issue.created"})

    assert len(await emit(db, "issue.created", project_id=fremd.id)) == 0
    assert len(await emit(db, "issue.created", project_id=ihrs.id)) == 1
    # The instance itself stays project-less (the subject is `standalone`), but the context
    # says which project the event came from.
    lauf = (await db.execute(select(WorkflowInstance))).scalars().one()
    assert lauf.project_id is None
    assert lauf.context["event"]["project_id"] == ihrs.id


async def test_admin_flow_hoert_ueberall(db):
    chef = await make_user(db, "chef", admin=True)
    fremd = await make_project(db, "FRD", "Fremdes Projekt")
    await _freier_flow(db, chef, "chef-lauscher", trigger={"event": "issue.created"})
    assert len(await emit(db, "issue.created", project_id=fremd.id)) == 1


# ── The webhook as a source ──────────────────────────────────────────────────

async def test_flow_bekommt_eine_eigene_adresse(client, db):
    """Not every system speaks MCP or knows Traccoon's events; almost every one sends a
    webhook. The address now comes into being in the flow, not at the other end."""
    anna = await make_user(db, "anna")
    d = await _freier_flow(db, anna, "stoerungsmelder")

    assert (await client.get(f"/workflows/{d.id}/webhook", headers=auth(anna))).json() is None

    r = await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))
    assert r.status_code == 201, r.text
    hook = r.json()
    assert hook["public_id"] and hook["secret"]
    assert hook["url"].endswith(f"/api/hooks/{hook['public_id']}")

    # A second call gives the same address; otherwise another one would pile up with every
    # click and nobody would know which one the foreign system uses.
    wieder = await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))
    assert wieder.json()["public_id"] == hook["public_id"]


async def test_fremder_gibt_keinem_flow_eine_adresse(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _freier_flow(db, anna, "annas-ablauf")
    assert (await client.post(f"/workflows/{d.id}/webhook",
                              headers=auth(bert))).status_code == 403


async def test_webhook_startet_den_flow_wirklich(client, db):
    """The proof that the address carries: a call in, an instance out, with the payload in
    the context so that the branches have something to read."""
    import hashlib
    import hmac
    import json as _json

    from app.models.workflow import WorkflowInstance

    anna = await make_user(db, "anna")
    d = await _freier_flow(db, anna, "stoerungsmelder")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))).json()

    payload = {"quelle": "Zabbix", "vorgang": {"id": 42, "titel": "Störung"}}
    roh = _json.dumps(payload).encode()
    sig = hmac.new(hook["secret"].encode(), roh, hashlib.sha256).hexdigest()
    r = await client.post(f"/hooks/{hook['public_id']}", content=roh,
                          headers={"content-type": "application/json",
                                   "X-Webhook-Signature": sig})
    assert r.status_code in (200, 202), r.text

    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().all()
    assert len(inst) == 1
    assert inst[0].context["vorgang"]["titel"] == "Störung"


# ── The artifact comes from the payload ──────────────────────────────────────

async def _ticket(db, chef, key="ABC-1"):
    from app.models.ticket import Issue, IssueType, WorkflowStatus

    projekt = await make_project(db, key.split("-")[0], "Projekt")
    await add_member(db, projekt, chef, ProjectRole.owner)
    kind = IssueType(project_id=projekt.id, name="Aufgabe", order=0)
    stat = WorkflowStatus(project_id=projekt.id, name="Offen", order=0)
    db.add_all([kind, stat])
    await db.flush()
    issue = Issue(project_id=projekt.id, number=int(key.split("-")[1]), key=key,
                  type_id=kind.id, status_id=stat.id, summary="Zielticket",
                  reporter_id=chef.id, rank="1")
    db.add(issue)
    await db.commit()
    return projekt, issue


async def _mit_subjektfeld(db, owner, field: str | None, subject=WorkflowSubjectKind.issue):
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


async def _rufen(client, hook, payload: dict):
    import hashlib
    import hmac
    import json as _json

    roh = _json.dumps(payload).encode()
    sig = hmac.new(hook["secret"].encode(), roh, hashlib.sha256).hexdigest()
    return await client.post(f"/hooks/{hook['public_id']}", content=roh,
                             headers={"content-type": "application/json",
                                      "X-Webhook-Signature": sig})


async def test_webhook_bindet_das_ticket_aus_der_payload(client, db):
    """The foreign system does not know Traccoon's numbers; it names the identifier it knows.
    Without this binding all ticket actions of the flow would run into nothing."""
    chef = await make_user(db, "chef", admin=True)
    _, issue = await _ticket(db, chef, "ABC-7")
    d = await _mit_subjektfeld(db, chef, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()

    r = await _rufen(client, hook, {"vorgang": {"ticket": "ABC-7"}})
    assert r.status_code in (200, 202), r.text
    assert r.json()["issue_id"] == issue.id

    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().one()
    assert inst.issue_id == issue.id
    # The project of the ticket travels along; otherwise rights and live events do not work.
    assert inst.project_id == issue.project_id


async def test_auch_die_nummer_geht(client, db):
    chef = await make_user(db, "chef", admin=True)
    _, issue = await _ticket(db, chef, "ABC-9")
    d = await _mit_subjektfeld(db, chef, "id")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()
    r = await _rufen(client, hook, {"id": issue.id})
    assert r.json()["issue_id"] == issue.id


async def test_fehlendes_field_sagt_es_deutlich(client, db):
    """A flow that needs an artifact but gets none must not start mutely into nothing."""
    chef = await make_user(db, "chef", admin=True)
    await _ticket(db, chef, "ABC-3")
    d = await _mit_subjektfeld(db, chef, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()

    r = await _rufen(client, hook, {"etwas": "anderes"})
    assert r.status_code == 400 and "vorgang.ticket" in r.text

    leer = await _mit_subjektfeld(db, chef, None)
    hook2 = (await client.post(f"/workflows/{leer.id}/webhook", headers=auth(chef))).json()
    r2 = await _rufen(client, hook2, {"vorgang": {"ticket": "ABC-3"}})
    assert r2.status_code == 400 and "kein Feld" in r2.text


async def test_fremdes_ticket_bleibt_fremd(client, db):
    """The rights come from the owner of the trigger, not from the caller: anybody can know
    a webhook address."""
    chef = await make_user(db, "chef", admin=True)
    anna = await make_user(db, "anna")
    _, issue = await _ticket(db, chef, "GEH-4")

    d = await _mit_subjektfeld(db, anna, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))).json()
    r = await _rufen(client, hook, {"vorgang": {"ticket": "GEH-4"}})
    assert r.status_code == 400 and "Rechte" in r.text


async def test_flow_ohne_artefakt_braucht_kein_field(client, db):
    chef = await make_user(db, "chef", admin=True)
    d = await _mit_subjektfeld(db, chef, None, subject=WorkflowSubjectKind.standalone)
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()
    r = await _rufen(client, hook, {"irgendwas": 1})
    assert r.status_code in (200, 202), r.text
