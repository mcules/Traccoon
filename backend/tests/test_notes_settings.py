"""The note area's settings, which are the house's settings.

There is no settings page of its own any more. What a person can decide sits on
the person, what the house decides sits in the house — a third place is how
somebody ends up looking for the same switch three times.
"""
from __future__ import annotations

import pytest

from app.notes import registry
from app.notes.settings import options as vault_options
from conftest import auth, make_user


@pytest.fixture(autouse=True)
def fresh():
    registry.forget_all()
    yield
    registry.forget_all()


@pytest.mark.asyncio
async def test_a_fresh_account_gets_the_defaults(client, db) -> None:
    user = await make_user(db, "notes1")
    r = await client.get("/notes-native/prefs", headers=auth(user))
    assert r.status_code == 200
    assert r.json() == vault_options.defaults()


@pytest.mark.asyncio
async def test_only_what_the_call_names_changes(client, db) -> None:
    user = await make_user(db, "notes2")
    await client.put("/notes-native/prefs", headers=auth(user),
                     json={"default_view": "reading"})
    await client.put("/notes-native/prefs", headers=auth(user),
                     json={"delete_mode": "permanent"})
    out = (await client.get("/notes-native/prefs", headers=auth(user))).json()
    assert out["default_view"] == "reading"
    assert out["delete_mode"] == "permanent"
    assert out["trash"] == ".trash"                      # untouched


@pytest.mark.asyncio
async def test_a_value_nobody_offers_falls_back_to_the_default(client, db) -> None:
    """Dropped rather than refused: a preference that arrived malformed must not
    be able to stop somebody reading their notes."""
    user = await make_user(db, "notes3")
    r = await client.put("/notes-native/prefs", headers=auth(user),
                         json={"default_view": "hologram", "delete_mode": "permanent"})
    assert r.status_code == 200
    assert r.json()["default_view"] == "live"
    assert r.json()["delete_mode"] == "permanent"


@pytest.mark.asyncio
async def test_the_preference_reaches_the_vault(tmp_path, client, db) -> None:
    """The point of the whole thing: what is set here decides what a delete does."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "A.md").write_text("eins\n", encoding="utf-8")
    user = await make_user(db, "notes4")
    user.vault_path = str(root)
    await db.commit()

    assert registry.workspace_of(user).options.delete_mode == "trash"
    await client.put("/notes-native/prefs", headers=auth(user),
                     json={"delete_mode": "permanent"})
    await db.refresh(user)
    assert registry.workspace_of(user).options.delete_mode == "permanent"


@pytest.mark.asyncio
async def test_the_workspace_is_kept_on_the_person(client, db) -> None:
    """Not in the browser: whoever logs in at the other machine in the evening
    carries on where they left off."""
    user = await make_user(db, "notes5")
    assert (await client.get("/notes-native/uistate", headers=auth(user))).json() == {}
    await client.put("/notes-native/uistate", headers=auth(user),
                     json={"tabs": ["A.md"], "panel": "search"})
    out = (await client.get("/notes-native/uistate", headers=auth(user))).json()
    assert out == {"tabs": ["A.md"], "panel": "search"}


# ------------------------------------------------------------------ calendars

@pytest.mark.asyncio
async def test_a_calendar_needs_an_address(client, db) -> None:
    user = await make_user(db, "cal1")
    r = await client.post("/notes-native/calendars", headers=auth(user),
                          json={"name": "Ohne"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_a_calendar_password_never_comes_back_out(client, db) -> None:
    """The interface only learns whether one is set, which is all it needs."""
    user = await make_user(db, "cal2")
    r = await client.post("/notes-native/calendars", headers=auth(user),
                          json={"name": "Privat", "url": "https://example/cal.ics",
                                "auth_user": "me", "auth_password": "geheim"})
    assert r.status_code == 201
    body = r.json()
    assert body["has_password"] is True
    assert "geheim" not in str(body)
    listed = (await client.get("/notes-native/calendars", headers=auth(user))).json()
    assert "geheim" not in str(listed)


@pytest.mark.asyncio
async def test_an_absent_password_is_left_alone_and_an_empty_one_clears_it(client, db) -> None:
    """Two different wishes, and a single field cannot carry both."""
    user = await make_user(db, "cal3")
    cid = (await client.post("/notes-native/calendars", headers=auth(user),
                             json={"url": "https://example/cal.ics",
                                   "auth_password": "geheim"})).json()["id"]
    after_rename = await client.patch(f"/notes-native/calendars/{cid}",
                                      headers=auth(user), json={"name": "Neu"})
    assert after_rename.json()["has_password"] is True
    cleared = await client.patch(f"/notes-native/calendars/{cid}",
                                 headers=auth(user), json={"auth_password": ""})
    assert cleared.json()["has_password"] is False


@pytest.mark.asyncio
async def test_somebody_elses_calendar_is_simply_not_there(client, db) -> None:
    """The same answer as for one that does not exist: telling the two apart
    would say that it does."""
    mine = await make_user(db, "cal4")
    theirs = await make_user(db, "cal5")
    cid = (await client.post("/notes-native/calendars", headers=auth(mine),
                             json={"url": "https://example/cal.ics"})).json()["id"]
    assert (await client.get("/notes-native/calendars",
                             headers=auth(theirs))).json()["calendars"] == []
    assert (await client.patch(f"/notes-native/calendars/{cid}", headers=auth(theirs),
                               json={"name": "meins jetzt"})).status_code == 404
    assert (await client.delete(f"/notes-native/calendars/{cid}",
                                headers=auth(theirs))).status_code == 404


@pytest.mark.asyncio
async def test_calendars_come_back_in_the_order_they_were_given(client, db) -> None:
    user = await make_user(db, "cal6")
    for i, name in enumerate(["dritter", "erster", "zweiter"]):
        await client.post("/notes-native/calendars", headers=auth(user),
                          json={"name": name, "url": f"https://example/{name}.ics",
                                "position": {"erster": 0, "zweiter": 1, "dritter": 2}[name]})
    out = (await client.get("/notes-native/calendars", headers=auth(user))).json()
    assert [c["name"] for c in out["calendars"]] == ["erster", "zweiter", "dritter"]
