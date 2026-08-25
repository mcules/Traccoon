"""Die Reihen ueber HTTP: aufnehmen, sehen, teilen.

The emphasis lies on what may go wrong: a foreign series has to be invisible (as a 404, not as
a 403 — otherwise the answer reveals its existence), a read grant must not be enough for
writing, and a newly issued token has to make the old one
sofort wertlos machen.
"""
from conftest import auth, make_user


async def _series(client, user, key="handy", kind="location", **remainder):
    r = await client.post("/series", headers=auth(user),
                          json={"key": key, "kind": kind, "name": key, **remainder})
    assert r.status_code == 201, r.text
    return r.json()


async def _token(client, user, key="handy") -> str:
    r = await client.post(f"/series/{key}/token", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ── Anlegen ──────────────────────────────────────────────────────────────────

async def test_creating_and_finding_again(client, db):
    me = await make_user(db, "ich")
    await _series(client, me, "handy")

    (r,) = (await client.get("/series", headers=auth(me))).json()
    assert (r["key"], r["kind"], r["own"], r["has_token"]) == ("handy", "location", True, False)


async def test_an_unknown_kind_is_rejected(client, db):
    me = await make_user(db, "ich")
    r = await client.post("/series", headers=auth(me), json={"key": "x", "kind": "bilder"})
    assert r.status_code == 400 and r.json()["key"] == "err.unknown_series_kind"


async def test_the_same_key_twice_does_not_work(client, db):
    me = await make_user(db, "ich")
    await _series(client, me, "handy")
    r = await client.post("/series", headers=auth(me),
                          json={"key": "handy", "kind": "location"})
    assert r.status_code == 409


async def test_two_people_may_have_the_same_key(client, db):
    """The key is unique per person, not across the whole house."""
    a = await make_user(db, "a")
    b = await make_user(db, "b")
    await _series(client, a, "handy")
    await _series(client, b, "handy")


# ── Aufnahme ─────────────────────────────────────────────────────────────────

async def test_all_four_formats_land_in_the_same_series(client, db):
    me = await make_user(db, "ich")
    await _series(client, me, "handy", settings={"min_distance_m": 0, "min_interval_s": 0})
    tok = await _token(client, me)

    # flach (Home Assistant)
    r = await client.post(f"/ingest/{tok}",
                          json={"lat": 50.08, "lon": 10.56, "battery": 80, "source": "ha"})
    assert r.status_code == 202 and r.json()["accepted"] == 1

    # OwnTracks
    r = await client.post(f"/ingest/{tok}",
                          json={"_type": "location", "lat": 50.09, "lon": 10.57, "batt": 70})
    assert r.json()["accepted"] == 1

    # Overland (Stapel, lon zuerst)
    r = await client.post(f"/ingest/{tok}", json={"locations": [
        {"geometry": {"coordinates": [10.58, 50.10]}, "properties": {"battery_level": 0.6}},
        {"geometry": {"coordinates": [10.59, 50.11]}, "properties": {}}]})
    assert r.json()["accepted"] == 2

    # Tracker payloads that come as a GET with everything in the address
    r = await client.get(f"/ingest/{tok}?id=handy&lat=50.12&lon=10.60&timestamp=1787227200")
    assert r.json()["accepted"] == 1

    data = (await client.get("/series/handy/points", headers=auth(me))).json()
    assert len(data["points"]) == 5
    assert {p["source"] for p in data["points"]} == {"ha", "owntracks", "overland", "traccar"}


async def test_a_report_without_a_position_is_not_an_error(client, db):
    """Sonst schickt ein Geraet dieselbe Nachricht in einer Wiederholungsschleife."""
    me = await make_user(db, "ich")
    await _series(client, me, "handy")
    tok = await _token(client, me)

    r = await client.post(f"/ingest/{tok}", json={"_type": "waypoints"})
    assert r.status_code == 202 and r.json()["ignored"] is True


async def test_an_unknown_token_reveals_nothing(client, db):
    r = await client.post("/ingest/trk_ausgedacht", json={"lat": 1, "lon": 1})
    assert r.status_code == 404


async def test_a_new_token_makes_the_old_one_worthless(client, db):
    me = await make_user(db, "ich")
    await _series(client, me, "handy")
    old = await _token(client, me)
    new = await _token(client, me)
    assert old != new

    assert (await client.post(f"/ingest/{old}", json={"lat": 50, "lon": 10})).status_code == 404
    assert (await client.post(f"/ingest/{new}", json={"lat": 50, "lon": 10})).status_code == 202


async def test_the_token_can_be_looked_at_again(client, db):
    """One has to type it into a phone — showing it once is not enough there."""
    me = await make_user(db, "ich")
    await _series(client, me, "handy")
    tok = await _token(client, me)

    r = await client.get("/series/handy/token", headers=auth(me))
    assert r.status_code == 200 and r.json()["token"] == tok


# ── Sehen und Teilen ─────────────────────────────────────────────────────────

async def test_a_foreign_series_is_invisible(client, db):
    me = await make_user(db, "ich")
    foreign = await make_user(db, "fremd")
    await _series(client, me, "handy")

    assert (await client.get("/series", headers=auth(foreign))).json() == []
    # 404 and not 403: a 403 would confirm that the series exists.
    r = await client.get("/series/handy/points", headers=auth(foreign))
    assert r.status_code == 404


async def test_a_grant_makes_it_visible(client, db):
    me = await make_user(db, "ich")
    friend = await make_user(db, "freund")
    await _series(client, me, "handy")

    r = await client.post("/series/handy/shares", headers=auth(me),
                          json={"user_id": friend.id, "level": "view"})
    assert r.status_code == 201

    (seen,) = (await client.get("/series", headers=auth(friend))).json()
    assert seen["key"] == "handy" and seen["own"] is False
    assert seen["owner"] == "Ich"
    assert (await client.get("/series/handy/points", headers=auth(friend))).status_code == 200


async def test_read_access_is_not_enough_to_change(client, db):
    me = await make_user(db, "ich")
    friend = await make_user(db, "freund")
    await _series(client, me, "handy")
    await client.post("/series/handy/shares", headers=auth(me),
                      json={"user_id": friend.id, "level": "view"})

    r = await client.put("/series/handy", headers=auth(friend), json={"name": "meins jetzt"})
    assert r.status_code == 403
    # No new token either: with that one could hijack the other person's series.
    assert (await client.post("/series/handy/token", headers=auth(friend))).status_code == 403


async def test_manage_may_change_but_not_redistribute(client, db):
    me = await make_user(db, "ich")
    friend = await make_user(db, "freund")
    third = await make_user(db, "dritter")
    await _series(client, me, "handy")
    await client.post("/series/handy/shares", headers=auth(me),
                      json={"user_id": friend.id, "level": "manage"})

    assert (await client.put("/series/handy", headers=auth(friend),
                             json={"name": "Handy neu"})).status_code == 200
    r = await client.post("/series/handy/shares", headers=auth(friend),
                          json={"user_id": third.id, "level": "view"})
    assert r.status_code == 403


async def test_withdrawing_a_grant(client, db):
    me = await make_user(db, "ich")
    friend = await make_user(db, "freund")
    await _series(client, me, "handy")
    r = await client.post("/series/handy/shares", headers=auth(me),
                          json={"user_id": friend.id, "level": "view"})
    sid = r.json()["id"]

    assert (await client.delete(f"/series/handy/shares/{sid}",
                                headers=auth(me))).status_code == 204
    assert (await client.get("/series", headers=auth(friend))).json() == []


# ── Live und Orte ────────────────────────────────────────────────────────────

async def test_live_shows_the_latest_state(client, db):
    me = await make_user(db, "ich")
    await _series(client, me, "handy")
    tok = await _token(client, me)
    await client.post(f"/ingest/{tok}", json={"lat": 50.08, "lon": 10.56, "battery": 42})

    (r,) = (await client.get("/series-live", headers=auth(me))).json()
    assert r["state"]["lat"] == 50.08 and r["state"]["battery"] == 42


async def test_a_series_called_live_does_not_displace_the_endpoint(client, db):
    """`{key:path}` is greedy — which is why the live state sits on an address of its own."""
    me = await make_user(db, "ich")
    await _series(client, me, "live")
    r = await client.get("/series-live", headers=auth(me))
    assert r.status_code == 200 and isinstance(r.json(), list)


async def test_creating_and_deleting_places(client, db):
    me = await make_user(db, "ich")
    r = await client.post("/places", headers=auth(me),
                          json={"key": "zuhause", "name": "Zuhause",
                                "lat": 50.0825, "lon": 10.5663, "radius_m": 120})
    assert r.status_code == 201
    pid = r.json()["id"]

    (o,) = (await client.get("/places", headers=auth(me))).json()
    assert o["radius_m"] == 120

    assert (await client.delete(f"/places/{pid}", headers=auth(me))).status_code == 204
    assert (await client.get("/places", headers=auth(me))).json() == []


async def test_foreign_places_stay_foreign(client, db):
    me = await make_user(db, "ich")
    foreign = await make_user(db, "fremd")
    r = await client.post("/places", headers=auth(me),
                          json={"key": "zuhause", "lat": 50.0, "lon": 10.0})
    pid = r.json()["id"]

    assert (await client.get("/places", headers=auth(foreign))).json() == []
    assert (await client.delete(f"/places/{pid}", headers=auth(foreign))).status_code == 404


async def test_deleting_a_point_updates_the_counter(client, db):
    me = await make_user(db, "ich")
    await _series(client, me, "handy", settings={"min_distance_m": 0, "min_interval_s": 0})
    tok = await _token(client, me)
    await client.post(f"/ingest/{tok}", json={"lat": 50.08, "lon": 10.56})
    await client.post(f"/ingest/{tok}", json={"lat": 50.09, "lon": 10.57})

    data = (await client.get("/series/handy/points", headers=auth(me))).json()
    assert data["series"]["points"] == 2
    first = data["points"][0]["id"]

    assert (await client.delete(f"/series/handy/points/{first}",
                                headers=auth(me))).status_code == 204
    data = (await client.get("/series/handy/points", headers=auth(me))).json()
    assert data["series"]["points"] == 1


async def test_renaming(client, db):
    """Names change. What does not work is a key that already exists."""
    me = await make_user(db, "ich")
    await _series(client, me, "handy.alt")
    await _series(client, me, "handy.belegt")

    r = await client.put("/series/handy.alt", headers=auth(me),
                         json={"key": "tracker.neu", "name": "Neu"})
    assert r.status_code == 200 and r.json()["key"] == "tracker.neu"
    assert (await client.get("/series/handy.alt/points", headers=auth(me))).status_code == 404
    assert (await client.get("/series/tracker.neu/points", headers=auth(me))).status_code == 200

    r = await client.put("/series/tracker.neu", headers=auth(me),
                         json={"key": "handy.belegt"})
    assert r.status_code == 409


async def test_renaming_keeps_points_and_token(client, db):
    me = await make_user(db, "ich")
    await _series(client, me, "handy.alt", settings={"min_distance_m": 0, "min_interval_s": 0})
    tok = await _token(client, me, "handy.alt")
    await client.post(f"/ingest/{tok}", json={"lat": 50.08, "lon": 10.56})

    await client.put("/series/handy.alt", headers=auth(me), json={"key": "tracker.neu"})

    data = (await client.get("/series/tracker.neu/points", headers=auth(me))).json()
    assert len(data["points"]) == 1
    # The token hangs on the series, not on the key — the device keeps reporting.
    r = await client.post(f"/ingest/{tok}", json={"lat": 50.09, "lon": 10.57})
    assert r.status_code == 202 and r.json()["accepted"] == 1
