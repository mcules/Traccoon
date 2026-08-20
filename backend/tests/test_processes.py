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


async def test_slots_zeigen_den_ausgelieferten_satz(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/slots", headers=auth(admin))
    assert r.status_code == 200, r.text
    daten = {s["slot"]: s for s in r.json()}
    assert set(daten) == set(sets.SLOT_META)
    lz = daten["ticket_lifecycle"]
    assert lz["published"] is True
    assert lz["version"] >= 1
    assert lz["abweichungen"] == []


async def test_projekt_kopie_erscheint_als_abweichung(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "ABW", "Abweichler")
    await add_member(db, proj, admin, ProjectRole.owner)
    await sets.customize(db, proj, "ticket_lifecycle", admin.id)
    await db.commit()

    r = await client.get("/processes/slots", headers=auth(admin))
    lz = next(s for s in r.json() if s["slot"] == "ticket_lifecycle")
    assert [a["project_key"] for a in lz["abweichungen"]] == ["ABW"]


async def test_fremde_projekte_bleiben_verborgen(client, db, standard):
    """A deviation would otherwise reveal names of projects that are none of one's business."""
    besitzer = await make_user(db, "eigner", admin=True)
    fremder = await make_user(db, "fremd")
    proj = await make_project(db, "GEH", "Geheim", inherit_members=False)
    await add_member(db, proj, besitzer, ProjectRole.owner)
    await sets.customize(db, proj, "ticket_lifecycle", besitzer.id)
    await db.commit()

    r = await client.get("/processes/slots", headers=auth(fremder))
    lz = next(s for s in r.json() if s["slot"] == "ticket_lifecycle")
    assert lz["abweichungen"] == []


# ── Betrieb ──────────────────────────────────────────────────────────────────

_lfd = itertools.count(1)


async def _instanz(db, proj, *, status, alter_stunden=0.0) -> WorkflowInstance:
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
    if alter_stunden:
        inst.started_at = (dt.datetime.now(tz=dt.timezone.utc)
                           - dt.timedelta(hours=alter_stunden))
    await db.commit()
    return inst


async def test_wartende_vorgaenge_gehoeren_in_die_betriebssicht(client, db):
    """`waiting` is the normal case: if the default view left it out, it would be blind."""
    user = await make_user(db, "op", admin=True)
    proj = await make_project(db, "OPS", "Betrieb")
    await add_member(db, proj, user, ProjectRole.owner)
    await _instanz(db, proj, status=WorkflowInstanceStatus.waiting)

    r = await client.get("/processes/running", headers=auth(user))
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()] == ["waiting"]


async def test_abgeschlossenes_bleibt_draussen_bis_man_es_will(client, db):
    user = await make_user(db, "op", admin=True)
    proj = await make_project(db, "OPS", "Betrieb")
    await add_member(db, proj, user, ProjectRole.owner)
    await _instanz(db, proj, status=WorkflowInstanceStatus.completed)

    assert (await client.get("/processes/running", headers=auth(user))).json() == []
    mit = await client.get("/processes/running?include_done=true", headers=auth(user))
    assert len(mit.json()) == 1


async def test_langes_warten_gilt_als_haengt(client, db):
    user = await make_user(db, "op", admin=True)
    proj = await make_project(db, "OPS", "Betrieb")
    await add_member(db, proj, user, ProjectRole.owner)
    await _instanz(db, proj, status=WorkflowInstanceStatus.waiting, alter_stunden=50)
    await _instanz(db, proj, status=WorkflowInstanceStatus.waiting)

    alle = (await client.get("/processes/running", headers=auth(user))).json()
    assert sorted(x["haengt"] for x in alle) == [False, True]
    nur = (await client.get("/processes/running?only_stuck=true", headers=auth(user))).json()
    assert len(nur) == 1 and nur[0]["haengt"] is True


async def test_fremde_vorgaenge_sind_unsichtbar(client, db):
    besitzer = await make_user(db, "eigner", admin=True)
    fremder = await make_user(db, "fremd")
    proj = await make_project(db, "GEH", "Geheim", inherit_members=False)
    await add_member(db, proj, besitzer, ProjectRole.owner)
    await _instanz(db, proj, status=WorkflowInstanceStatus.waiting)

    assert (await client.get("/processes/running", headers=auth(fremder))).json() == []


# ── Triggers ─────────────────────────────────────────────────────────────────

async def test_ausloeser_findet_unterprozess_und_manuelles(client, db, standard):
    """The acceptance flow is called by the lifecycle; otherwise it would look triggerless."""
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/triggers", headers=auth(admin))
    assert r.status_code == 200, r.text
    daten = r.json()
    abnahme = [t for t in daten if t["slot"] == "acceptance"]
    assert abnahme and abnahme[0]["kind"] == "subflow"
    assert "KI-Ticket-Lebenszyklus" in abnahme[0]["label"]
    # Every published flow turns up exactly once.
    assert {t["slot"] for t in daten} >= set(sets.SLOT_META)


async def test_ereignisse_zaehlen_ihre_zuhoerer(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/events", headers=auth(admin))
    assert r.status_code == 200
    daten = r.json()
    assert {e["event"] for e in daten}
    # Without a set trigger nobody listens, and the overview should show that honestly. The
    # shipped set contains no event driven flow any more: the mail inbox left it and is now
    # a template one creates for oneself.
    zuhoerer = {e["event"]: e["listeners"] for e in daten}
    assert all(z == 0 for z in zuhoerer.values())


# ── Rolling back ─────────────────────────────────────────────────────────────

async def test_zurueckrollen_legt_eine_neue_version_an(client, db, standard):
    """The history stays: rolling back happens by publishing, not by bending a pointer."""
    admin = await make_user(db, "chef", admin=True)
    d = await sets.set_definition(db, standard.id, "ticket_lifecycle")
    erste = await db.get(WorkflowVersion, d.current_version_id)
    graph = dict(erste.graph)

    zweite = WorkflowVersion(definition_id=d.id, version=erste.version + 1, graph=graph,
                             status=WorkflowVersionStatus.published,
                             published_at=dt.datetime.now(tz=dt.timezone.utc))
    db.add(zweite)
    await db.flush()
    d.current_version_id = zweite.id
    await db.commit()

    r = await client.post(f"/workflows/{d.id}/versions/{erste.id}/rollback", headers=auth(admin))
    assert r.status_code == 200, r.text
    neu = r.json()
    assert neu["version"] == zweite.version + 1
    assert f"{erste.version}" in neu["notes"]
    await db.refresh(d)
    assert d.current_version_id == neu["id"]
    # The old version is untouched: running instances hang off it.
    await db.refresh(erste)
    assert erste.status == WorkflowVersionStatus.published


async def test_zurueckrollen_auf_die_aktuelle_fassung_ist_ein_konflikt(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    d = await sets.set_definition(db, standard.id, "ticket_lifecycle")
    r = await client.post(f"/workflows/{d.id}/versions/{d.current_version_id}/rollback",
                          headers=auth(admin))
    assert r.status_code == 409


async def test_nur_ein_admin_darf_den_standard_zurueckrollen(client, db, standard):
    niemand = await make_user(db, "gast")
    d = await sets.set_definition(db, standard.id, "ticket_lifecycle")
    erste = await db.get(WorkflowVersion, d.current_version_id)
    r = await client.post(f"/workflows/{d.id}/versions/{erste.id}/rollback", headers=auth(niemand))
    assert r.status_code == 403


async def test_kontextfelder_nennen_ihre_herkunft(client, db):
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


async def test_kontextfelder_decken_die_guards_des_standardsatzes(client, db):
    """What the shipped flows read at their branches has to stand in the catalog; otherwise it
    describes something other than what runs."""
    from app.services.workflow_seed import BUILDERS

    anna = await make_user(db, "anna")
    k = (await client.get("/workflow-context-fields", headers=auth(anna))).json()
    bekannt = {f["path"] for gruppe in ("base",) for f in k[gruppe]}
    for topf in ("triggers", "actions", "nodes"):
        for felder in k[topf].values():
            bekannt |= {f["path"] for f in felder}

    def vars_von(regel, raus: set):
        if isinstance(regel, dict):
            for op, args in regel.items():
                if op == "var" and isinstance(args, str):
                    raus.add(args)
                else:
                    vars_von(args, raus)
        elif isinstance(regel, list):
            for a in regel:
                vars_von(a, raus)

    benutzt: set[str] = set()
    for build in BUILDERS.values():
        for n in build()["nodes"]:
            cfg = (n.get("data") or {}).get("config") or {}
            for b in cfg.get("branches") or []:
                vars_von(b.get("guard"), benutzt)

    # `entry` controls the entry of the lifecycle and comes from the caller, not from an
    # action; the rest has to stand in the catalog.
    fehlend = {v for v in benutzt if v not in bekannt and v != "entry"}
    assert not fehlend, f"Guards read fields the catalog does not know: {sorted(fehlend)}"
