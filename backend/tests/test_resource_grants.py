"""Sicherheitsrelevante Pfade der granularen Freigaben (ABC-23) und der
Projekt-Rollen-Vererbung entlang des parent_id-Baums (ABC-22).

Deckt den „Wart"-Fall ab: ein Nutzer ohne Projekt-Mitgliedschaft sieht/verwaltet
ausschließlich das ihm freigegebene Wasserhäuschen samt der Masten darunter.
"""
from app.models.enums import GrantLevel, ProjectRole, ResourceType
from app.models.project import ResourceGrant
from conftest import add_member, auth, make_asset, make_location, make_project, make_user


# ── Vergeben / Entziehen ─────────────────────────────────────────────────────

async def test_maintainer_darf_grant_vergeben_und_entziehen(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    loc = await make_location(db, "Wasserhäuschen", project=proj)

    r = await client.post(
        f"/projects/{proj.id}/resource-grants",
        json={"user_id": guest.id, "resource_type": "location", "resource_id": loc.id,
              "level": "view", "recursive": True},
        headers=auth(owner),
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    assert r.json()["resource_label"] == "Wasserhäuschen"

    listed = await client.get(f"/projects/{proj.id}/resource-grants", headers=auth(owner))
    assert [g["id"] for g in listed.json()] == [gid]

    r = await client.delete(f"/projects/{proj.id}/resource-grants/{gid}", headers=auth(owner))
    assert r.status_code == 204
    listed = await client.get(f"/projects/{proj.id}/resource-grants", headers=auth(owner))
    assert listed.json() == []


async def test_member_darf_keine_grants_vergeben(client, db):
    owner = await make_user(db, "owner")
    plain = await make_user(db, "plain")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    await add_member(db, proj, plain, ProjectRole.member)
    loc = await make_location(db, "Wasserhäuschen", project=proj)

    r = await client.post(
        f"/projects/{proj.id}/resource-grants",
        json={"user_id": guest.id, "resource_type": "location", "resource_id": loc.id},
        headers=auth(plain),
    )
    assert r.status_code == 403


# ── Validierung ──────────────────────────────────────────────────────────────

async def test_unbekannter_user_und_objekt_werden_abgewiesen(client, db):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)

    r = await client.post(
        f"/projects/{proj.id}/resource-grants",
        json={"user_id": 9999, "resource_type": "location", "resource_id": 1},
        headers=auth(owner),
    )
    assert r.status_code == 404

    guest = await make_user(db, "guest")
    r = await client.post(
        f"/projects/{proj.id}/resource-grants",
        json={"user_id": guest.id, "resource_type": "location", "resource_id": 9999},
        headers=auth(owner),
    )
    assert r.status_code == 400


async def test_objekt_aus_fremdem_projekt_wird_abgewiesen(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    mine = await make_project(db, "WRT", "Wart")
    other = await make_project(db, "FRD", "Fremd")
    await add_member(db, mine, owner, ProjectRole.owner)
    foreign_loc = await make_location(db, "Fremd-Ort", project=other)

    r = await client.post(
        f"/projects/{mine.id}/resource-grants",
        json={"user_id": guest.id, "resource_type": "location", "resource_id": foreign_loc.id},
        headers=auth(owner),
    )
    assert r.status_code == 400


async def test_doppelter_grant_gibt_409(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    loc = await make_location(db, "Wasserhäuschen", project=proj)
    body = {"user_id": guest.id, "resource_type": "location", "resource_id": loc.id}

    assert (await client.post(f"/projects/{proj.id}/resource-grants", json=body,
                              headers=auth(owner))).status_code == 201
    assert (await client.post(f"/projects/{proj.id}/resource-grants", json=body,
                              headers=auth(owner))).status_code == 409


async def test_recursive_wird_bei_assets_normalisiert(client, db):
    """`recursive` ist bei Exemplaren bedeutungslos → immer False ablegen."""
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    asset = await make_asset(db, "Mast", project=proj)

    r = await client.post(
        f"/projects/{proj.id}/resource-grants",
        json={"user_id": guest.id, "resource_type": "asset", "resource_id": asset.id,
              "recursive": True},
        headers=auth(owner),
    )
    assert r.status_code == 201
    assert r.json()["recursive"] is False


# ── Zugriff über Grants ──────────────────────────────────────────────────────

async def test_grant_user_sieht_nur_freigegebenes_asset(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    mast = await make_asset(db, "Mast 1", project=proj)
    haus = await make_asset(db, "Pumpe", project=proj)

    db.add(ResourceGrant(project_id=proj.id, user_id=guest.id,
                         resource_type=ResourceType.asset, resource_id=mast.id,
                         level=GrantLevel.view, recursive=False))
    await db.commit()

    r = await client.get("/hardware/assets", headers=auth(guest))
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()]
    assert mast.id in ids and haus.id not in ids


async def test_ohne_grant_und_ohne_mitgliedschaft_nichts_sichtbar(client, db):
    owner = await make_user(db, "owner")
    outsider = await make_user(db, "outsider")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    await make_asset(db, "Mast 1", project=proj)
    await make_location(db, "Wasserhäuschen", project=proj)

    assert (await client.get("/hardware/assets", headers=auth(outsider))).json() == []
    assert (await client.get("/locations", headers=auth(outsider))).json() == []


async def test_location_grant_rekursiv_deckt_kind_ort(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    haus = await make_location(db, "Wasserhäuschen", project=proj)
    mast = await make_location(db, "Mast 1", project=proj, parent=haus)

    db.add(ResourceGrant(project_id=proj.id, user_id=guest.id,
                         resource_type=ResourceType.location, resource_id=haus.id,
                         level=GrantLevel.view, recursive=True))
    await db.commit()

    ids = [loc["id"] for loc in (await client.get("/locations", headers=auth(guest))).json()]
    assert haus.id in ids and mast.id in ids


async def test_location_grant_ohne_rekursion_deckt_kind_ort_nicht(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    haus = await make_location(db, "Wasserhäuschen", project=proj)
    mast = await make_location(db, "Mast 1", project=proj, parent=haus)

    db.add(ResourceGrant(project_id=proj.id, user_id=guest.id,
                         resource_type=ResourceType.location, resource_id=haus.id,
                         level=GrantLevel.view, recursive=False))
    await db.commit()

    ids = [loc["id"] for loc in (await client.get("/locations", headers=auth(guest))).json()]
    assert haus.id in ids and mast.id not in ids


async def test_view_grant_erlaubt_kein_schreiben_manage_schon(client, db):
    owner = await make_user(db, "owner")
    viewer = await make_user(db, "viewer")
    manager = await make_user(db, "manager")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    asset = await make_asset(db, "Mast 1", project=proj)

    db.add(ResourceGrant(project_id=proj.id, user_id=viewer.id,
                         resource_type=ResourceType.asset, resource_id=asset.id,
                         level=GrantLevel.view, recursive=False))
    db.add(ResourceGrant(project_id=proj.id, user_id=manager.id,
                         resource_type=ResourceType.asset, resource_id=asset.id,
                         level=GrantLevel.manage, recursive=False))
    await db.commit()

    r = await client.put(f"/hardware/assets/{asset.id}", json={"notes": "x"},
                         headers=auth(viewer))
    assert r.status_code == 403
    r = await client.put(f"/hardware/assets/{asset.id}", json={"notes": "x"},
                         headers=auth(manager))
    assert r.status_code == 200


async def test_manage_grant_auf_ort_erlaubt_asset_verwaltung(client, db):
    owner = await make_user(db, "owner")
    manager = await make_user(db, "manager")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    haus = await make_location(db, "Wasserhäuschen", project=proj)
    asset = await make_asset(db, "Mast 1", project=proj, location=haus)

    db.add(ResourceGrant(project_id=proj.id, user_id=manager.id,
                         resource_type=ResourceType.location, resource_id=haus.id,
                         level=GrantLevel.manage, recursive=True))
    await db.commit()

    r = await client.put(f"/hardware/assets/{asset.id}", json={"notes": "geprüft"},
                         headers=auth(manager))
    assert r.status_code == 200, r.text


# ── Projekt-Rollen-Vererbung (Teil A) ────────────────────────────────────────

async def test_rolle_wird_an_subprojekt_vererbt_owner_gecappt(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Freifunk Haßberge")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, regio, ProjectRole.owner)

    r = await client.get(f"/projects/{sub.id}", headers=auth(regio))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["my_role"] == "maintainer"     # Owner wird bei Vererbung gecappt
    assert body["my_role_inherited"] is True
    assert body["is_member"] is True           # geerbt zählt als Mitglied, nicht als "fremd"


async def test_vererbung_abschaltbar(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Freifunk Haßberge")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id, inherit_members=False)
    await add_member(db, top, regio, ProjectRole.owner)

    assert (await client.get(f"/projects/{sub.id}", headers=auth(regio))).status_code == 404


async def test_direkte_rolle_schlaegt_geerbte(client, db):
    user = await make_user(db, "u")
    top = await make_project(db, "FFB", "Freifunk Haßberge")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, user, ProjectRole.owner)
    await add_member(db, sub, user, ProjectRole.viewer)

    body = (await client.get(f"/projects/{sub.id}", headers=auth(user))).json()
    assert body["my_role"] == "viewer"
    assert body["my_role_inherited"] is False


async def test_geerbte_rolle_sieht_hardware_des_subprojekts(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Freifunk Haßberge")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, regio, ProjectRole.owner)
    asset = await make_asset(db, "Mast 1", project=sub)

    ids = [a["id"] for a in (await client.get("/hardware/assets", headers=auth(regio))).json()]
    assert asset.id in ids


async def test_list_projects_enthaelt_geerbte_subprojekte(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Freifunk Haßberge")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await make_project(db, "FRD", "Fremd")
    await add_member(db, top, regio, ProjectRole.owner)

    keys = {p["key"] for p in (await client.get("/projects", headers=auth(regio))).json()}
    assert keys == {top.key, sub.key}


# ── Zyklus-Schutz beim Umhängen ──────────────────────────────────────────────

async def test_parent_darf_kein_nachfahre_sein(client, db):
    owner = await make_user(db, "owner")
    top = await make_project(db, "FFB", "Freifunk Haßberge")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, owner, ProjectRole.owner)

    r = await client.put(f"/projects/{top.id}", json={"parent_id": sub.id}, headers=auth(owner))
    assert r.status_code == 400
    r = await client.put(f"/projects/{top.id}", json={"parent_id": top.id}, headers=auth(owner))
    assert r.status_code == 400
