"""Security relevant paths of the granular grants (ABC-23) and of the project role
inheritance along the parent_id tree (ABC-22).

Covers the caretaker case: a user without a project membership sees and manages exclusively
the pump house granted to them including the masts below it.
"""
from app.models.enums import GrantLevel, ProjectRole, ResourceType
from app.models.project import ResourceGrant
from conftest import add_member, auth, make_asset, make_location, make_project, make_user


# ── Vergeben / Entziehen ─────────────────────────────────────────────────────

async def test_a_maintainer_may_grant_and_withdraw(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    loc = await make_location(db, "Water tower", project=proj)

    r = await client.post(
        f"/projects/{proj.id}/resource-grants",
        json={"user_id": guest.id, "resource_type": "location", "resource_id": loc.id,
              "level": "view", "recursive": True},
        headers=auth(owner),
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    assert r.json()["resource_label"] == "Water tower"

    listed = await client.get(f"/projects/{proj.id}/resource-grants", headers=auth(owner))
    assert [g["id"] for g in listed.json()] == [gid]

    r = await client.delete(f"/projects/{proj.id}/resource-grants/{gid}", headers=auth(owner))
    assert r.status_code == 204
    listed = await client.get(f"/projects/{proj.id}/resource-grants", headers=auth(owner))
    assert listed.json() == []


async def test_a_member_may_not_grant(client, db):
    owner = await make_user(db, "owner")
    plain = await make_user(db, "plain")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    await add_member(db, proj, plain, ProjectRole.member)
    loc = await make_location(db, "Water tower", project=proj)

    r = await client.post(
        f"/projects/{proj.id}/resource-grants",
        json={"user_id": guest.id, "resource_type": "location", "resource_id": loc.id},
        headers=auth(plain),
    )
    assert r.status_code == 403


# ── Validierung ──────────────────────────────────────────────────────────────

async def test_an_unknown_user_and_object_are_rejected(client, db):
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


async def test_an_object_from_a_foreign_project_is_rejected(client, db):
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


async def test_a_duplicate_grant_gives_409(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    loc = await make_location(db, "Water tower", project=proj)
    body = {"user_id": guest.id, "resource_type": "location", "resource_id": loc.id}

    assert (await client.post(f"/projects/{proj.id}/resource-grants", json=body,
                              headers=auth(owner))).status_code == 201
    assert (await client.post(f"/projects/{proj.id}/resource-grants", json=body,
                              headers=auth(owner))).status_code == 409


async def test_recursive_is_normalised_for_assets(client, db):
    """`recursive` is meaningless with units, so always store False."""
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


# ── Access over grants ───────────────────────────────────────────────────────

async def test_a_granted_user_sees_only_the_released_asset(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    mast = await make_asset(db, "Mast 1", project=proj)
    house = await make_asset(db, "Pumpe", project=proj)

    db.add(ResourceGrant(project_id=proj.id, user_id=guest.id,
                         resource_type=ResourceType.asset, resource_id=mast.id,
                         level=GrantLevel.view, recursive=False))
    await db.commit()

    r = await client.get("/hardware/assets", headers=auth(guest))
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()]
    assert mast.id in ids and house.id not in ids


async def test_without_a_grant_and_without_membership_nothing_is_visible(client, db):
    owner = await make_user(db, "owner")
    outsider = await make_user(db, "outsider")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    await make_asset(db, "Mast 1", project=proj)
    await make_location(db, "Water tower", project=proj)

    assert (await client.get("/hardware/assets", headers=auth(outsider))).json() == []
    assert (await client.get("/locations", headers=auth(outsider))).json() == []


async def test_a_recursive_location_grant_covers_the_child_location(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    house = await make_location(db, "Water tower", project=proj)
    mast = await make_location(db, "Mast 1", project=proj, parent=house)

    db.add(ResourceGrant(project_id=proj.id, user_id=guest.id,
                         resource_type=ResourceType.location, resource_id=house.id,
                         level=GrantLevel.view, recursive=True))
    await db.commit()

    ids = [loc["id"] for loc in (await client.get("/locations", headers=auth(guest))).json()]
    assert house.id in ids and mast.id in ids


async def test_a_non_recursive_location_grant_does_not_cover_the_child_location(client, db):
    owner = await make_user(db, "owner")
    guest = await make_user(db, "guest")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    house = await make_location(db, "Water tower", project=proj)
    mast = await make_location(db, "Mast 1", project=proj, parent=house)

    db.add(ResourceGrant(project_id=proj.id, user_id=guest.id,
                         resource_type=ResourceType.location, resource_id=house.id,
                         level=GrantLevel.view, recursive=False))
    await db.commit()

    ids = [loc["id"] for loc in (await client.get("/locations", headers=auth(guest))).json()]
    assert house.id in ids and mast.id not in ids


async def test_a_view_grant_allows_no_writing_a_manage_grant_does(client, db):
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


async def test_a_manage_grant_on_a_location_allows_managing_its_assets(client, db):
    owner = await make_user(db, "owner")
    manager = await make_user(db, "manager")
    proj = await make_project(db, "WRT", "Wart")
    await add_member(db, proj, owner, ProjectRole.owner)
    house = await make_location(db, "Water tower", project=proj)
    asset = await make_asset(db, "Mast 1", project=proj, location=house)

    db.add(ResourceGrant(project_id=proj.id, user_id=manager.id,
                         resource_type=ResourceType.location, resource_id=house.id,
                         level=GrantLevel.manage, recursive=True))
    await db.commit()

    r = await client.put(f"/hardware/assets/{asset.id}", json={"notes": "checked"},
                         headers=auth(manager))
    assert r.status_code == 200, r.text


# ── Projekt-Rollen-Vererbung (Teil A) ────────────────────────────────────────

async def test_the_role_is_inherited_by_the_subproject_owner_capped(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Community Network")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, regio, ProjectRole.owner)

    r = await client.get(f"/projects/{sub.id}", headers=auth(regio))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["my_role"] == "maintainer"     # the owner is capped on inheritance
    assert body["my_role_inherited"] is True
    assert body["is_member"] is True           # inherited counts as a member, not as "foreign"


async def test_inheritance_can_be_switched_off(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Community Network")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id, inherit_members=False)
    await add_member(db, top, regio, ProjectRole.owner)

    assert (await client.get(f"/projects/{sub.id}", headers=auth(regio))).status_code == 404


async def test_a_direct_role_beats_an_inherited_one(client, db):
    user = await make_user(db, "u")
    top = await make_project(db, "FFB", "Community Network")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, user, ProjectRole.owner)
    await add_member(db, sub, user, ProjectRole.viewer)

    body = (await client.get(f"/projects/{sub.id}", headers=auth(user))).json()
    assert body["my_role"] == "viewer"
    assert body["my_role_inherited"] is False


async def test_an_inherited_role_sees_the_hardware_of_the_subproject(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Community Network")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, regio, ProjectRole.owner)
    asset = await make_asset(db, "Mast 1", project=sub)

    ids = [a["id"] for a in (await client.get("/hardware/assets", headers=auth(regio))).json()]
    assert asset.id in ids


async def test_listing_projects_includes_inherited_subprojects(client, db):
    regio = await make_user(db, "regio")
    top = await make_project(db, "FFB", "Community Network")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await make_project(db, "FRD", "Fremd")
    await add_member(db, top, regio, ProjectRole.owner)

    keys = {p["key"] for p in (await client.get("/projects", headers=auth(regio))).json()}
    assert keys == {top.key, sub.key}


# ── Cycle protection while rehanging ─────────────────────────────────────────

async def test_a_parent_may_not_be_a_descendant(client, db):
    owner = await make_user(db, "owner")
    top = await make_project(db, "FFB", "Community Network")
    sub = await make_project(db, "WRT", "Wart", parent_id=top.id)
    await add_member(db, top, owner, ProjectRole.owner)

    r = await client.put(f"/projects/{top.id}", json={"parent_id": sub.id}, headers=auth(owner))
    assert r.status_code == 400
    r = await client.put(f"/projects/{top.id}", json={"parent_id": top.id}, headers=auth(owner))
    assert r.status_code == 400
