"""Prozess-Verwaltung: Standard-Satz mit Abweichungen, Betrieb, Auslöser, Zurückrollen.

Die vier Fragen, die eine Verwaltung beantworten muss — und die Grenze, die dabei nie fallen
darf: ein Nutzer sieht nur Projekte, auf die er Zugriff hat.
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
    """Ausgelieferten Satz anlegen — Grundlage aller Abweichungs-Prüfungen."""
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
    """Eine Abweichung verrät sonst Namen von Projekten, die einen nichts angehen."""
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
    # Schlüssel je Projekt eindeutig — ein Test legt mehrere Abläufe an.
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
    """`waiting` ist der Normalfall — würde die Standardsicht ihn weglassen, wäre sie blind."""
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


# ── Auslöser ─────────────────────────────────────────────────────────────────

async def test_ausloeser_findet_unterprozess_und_manuelles(client, db, standard):
    """Der Abnahme-Ablauf wird vom Lebenszyklus aufgerufen — sonst wirkte er auslöserlos."""
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/triggers", headers=auth(admin))
    assert r.status_code == 200, r.text
    daten = r.json()
    abnahme = [t for t in daten if t["slot"] == "acceptance"]
    assert abnahme and abnahme[0]["kind"] == "subflow"
    assert "KI-Ticket-Lebenszyklus" in abnahme[0]["label"]
    # Jeder veröffentlichte Ablauf taucht genau einmal auf.
    assert {t["slot"] for t in daten} >= set(sets.SLOT_META)


async def test_ereignisse_zaehlen_ihre_zuhoerer(client, db, standard):
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/processes/events", headers=auth(admin))
    assert r.status_code == 200
    daten = r.json()
    assert {e["event"] for e in daten}
    # Ohne gesetzten Trigger hört niemand zu — das soll die Übersicht ehrlich zeigen.
    assert all(e["listeners"] == 0 for e in daten)


# ── Zurückrollen ─────────────────────────────────────────────────────────────

async def test_zurueckrollen_legt_eine_neue_version_an(client, db, standard):
    """Historie bleibt: zurückgerollt wird durch Veröffentlichen, nicht durch Umbiegen."""
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
    # Die alte Fassung ist unangetastet — laufende Instanzen hängen daran.
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
