"""Die Reihen ueber HTTP: aufnehmen, sehen, teilen.

Der Schwerpunkt liegt auf dem, was schiefgehen darf: Eine fremde Reihe muss unsichtbar sein
(und zwar als 404, nicht als 403 — sonst verraet die Antwort ihre Existenz), eine
Lese-Freigabe darf nicht zum Schreiben reichen, und ein neu vergebenes Token muss das alte
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

async def test_create_und_wiederfinden(client, db):
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy")

    (r,) = (await client.get("/series", headers=auth(ich))).json()
    assert (r["key"], r["kind"], r["own"], r["has_token"]) == ("handy", "location", True, False)


async def test_unbekannte_kind_wird_abgewiesen(client, db):
    ich = await make_user(db, "ich")
    r = await client.post("/series", headers=auth(ich), json={"key": "x", "kind": "bilder"})
    assert r.status_code == 400 and r.json()["key"] == "err.unknown_series_kind"


async def test_zweimal_derselbe_key_geht_nicht(client, db):
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy")
    r = await client.post("/series", headers=auth(ich),
                          json={"key": "handy", "kind": "location"})
    assert r.status_code == 409


async def test_zwei_menschen_duerfen_denselben_key_haben(client, db):
    """Der Schluessel ist je Mensch eindeutig, nicht im ganzen Haus."""
    a = await make_user(db, "a")
    b = await make_user(db, "b")
    await _series(client, a, "handy")
    await _series(client, b, "handy")


# ── Aufnahme ─────────────────────────────────────────────────────────────────

async def test_all_vier_formate_landen_in_derselben_series(client, db):
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy", settings={"min_distance_m": 0, "min_interval_s": 0})
    tok = await _token(client, ich)

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

    # Traccar/OsmAnd (GET, alles in der Adresse)
    r = await client.get(f"/ingest/{tok}?id=handy&lat=50.12&lon=10.60&timestamp=1787227200")
    assert r.json()["accepted"] == 1

    daten = (await client.get("/series/handy/points", headers=auth(ich))).json()
    assert len(daten["points"]) == 5
    assert {p["source"] for p in daten["points"]} == {"ha", "owntracks", "overland", "traccar"}


async def test_notice_ohne_position_ist_kein_error(client, db):
    """Sonst schickt ein Geraet dieselbe Nachricht in einer Wiederholungsschleife."""
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy")
    tok = await _token(client, ich)

    r = await client.post(f"/ingest/{tok}", json={"_type": "waypoints"})
    assert r.status_code == 202 and r.json()["ignored"] is True


async def test_unbekanntes_token_verraet_nichts(client, db):
    r = await client.post("/ingest/trk_ausgedacht", json={"lat": 1, "lon": 1})
    assert r.status_code == 404


async def test_neues_token_macht_das_alte_wertlos(client, db):
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy")
    alt = await _token(client, ich)
    new = await _token(client, ich)
    assert alt != new

    assert (await client.post(f"/ingest/{alt}", json={"lat": 50, "lon": 10})).status_code == 404
    assert (await client.post(f"/ingest/{new}", json={"lat": 50, "lon": 10})).status_code == 202


async def test_token_laesst_sich_wieder_ansehen(client, db):
    """Man muss es in ein Telefon eintragen — einmal zeigen reicht da nicht."""
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy")
    tok = await _token(client, ich)

    r = await client.get("/series/handy/token", headers=auth(ich))
    assert r.status_code == 200 and r.json()["token"] == tok


# ── Sehen und Teilen ─────────────────────────────────────────────────────────

async def test_fremde_series_ist_unsichtbar(client, db):
    ich = await make_user(db, "ich")
    fremd = await make_user(db, "fremd")
    await _series(client, ich, "handy")

    assert (await client.get("/series", headers=auth(fremd))).json() == []
    # 404 und nicht 403: Eine 403 wuerde bestaetigen, dass es die Reihe gibt.
    r = await client.get("/series/handy/points", headers=auth(fremd))
    assert r.status_code == 404


async def test_grant_macht_visible(client, db):
    ich = await make_user(db, "ich")
    freund = await make_user(db, "freund")
    await _series(client, ich, "handy")

    r = await client.post("/series/handy/shares", headers=auth(ich),
                          json={"user_id": freund.id, "level": "view"})
    assert r.status_code == 201

    (seen,) = (await client.get("/series", headers=auth(freund))).json()
    assert seen["key"] == "handy" and seen["own"] is False
    assert seen["owner"] == "Ich"
    assert (await client.get("/series/handy/points", headers=auth(freund))).status_code == 200


async def test_read_reicht_nicht_zum_update(client, db):
    ich = await make_user(db, "ich")
    freund = await make_user(db, "freund")
    await _series(client, ich, "handy")
    await client.post("/series/handy/shares", headers=auth(ich),
                      json={"user_id": freund.id, "level": "view"})

    r = await client.put("/series/handy", headers=auth(freund), json={"name": "meins jetzt"})
    assert r.status_code == 403
    # Auch kein neues Token: Damit koennte man die Reihe des anderen kapern.
    assert (await client.post("/series/handy/token", headers=auth(freund))).status_code == 403


async def test_manage_may_update_aber_nicht_weiterverteilen(client, db):
    ich = await make_user(db, "ich")
    freund = await make_user(db, "freund")
    dritter = await make_user(db, "dritter")
    await _series(client, ich, "handy")
    await client.post("/series/handy/shares", headers=auth(ich),
                      json={"user_id": freund.id, "level": "manage"})

    assert (await client.put("/series/handy", headers=auth(freund),
                             json={"name": "Handy neu"})).status_code == 200
    r = await client.post("/series/handy/shares", headers=auth(freund),
                          json={"user_id": dritter.id, "level": "view"})
    assert r.status_code == 403


async def test_grant_zuruecknehmen(client, db):
    ich = await make_user(db, "ich")
    freund = await make_user(db, "freund")
    await _series(client, ich, "handy")
    r = await client.post("/series/handy/shares", headers=auth(ich),
                          json={"user_id": freund.id, "level": "view"})
    sid = r.json()["id"]

    assert (await client.delete(f"/series/handy/shares/{sid}",
                                headers=auth(ich))).status_code == 204
    assert (await client.get("/series", headers=auth(freund))).json() == []


# ── Live und Orte ────────────────────────────────────────────────────────────

async def test_live_zeigt_den_letzten_state(client, db):
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy")
    tok = await _token(client, ich)
    await client.post(f"/ingest/{tok}", json={"lat": 50.08, "lon": 10.56, "battery": 42})

    (r,) = (await client.get("/series-live", headers=auth(ich))).json()
    assert r["state"]["lat"] == 50.08 and r["state"]["battery"] == 42


async def test_series_namens_live_verdraengt_den_endpunkt_nicht(client, db):
    """`{key:path}` ist gierig — deshalb liegt der Live-Stand auf einer eigenen Adresse."""
    ich = await make_user(db, "ich")
    await _series(client, ich, "live")
    r = await client.get("/series-live", headers=auth(ich))
    assert r.status_code == 200 and isinstance(r.json(), list)


async def test_orte_create_und_delete(client, db):
    ich = await make_user(db, "ich")
    r = await client.post("/places", headers=auth(ich),
                          json={"key": "zuhause", "name": "Zuhause",
                                "lat": 50.0825, "lon": 10.5663, "radius_m": 120})
    assert r.status_code == 201
    pid = r.json()["id"]

    (o,) = (await client.get("/places", headers=auth(ich))).json()
    assert o["radius_m"] == 120

    assert (await client.delete(f"/places/{pid}", headers=auth(ich))).status_code == 204
    assert (await client.get("/places", headers=auth(ich))).json() == []


async def test_fremde_orte_bleiben_fremd(client, db):
    ich = await make_user(db, "ich")
    fremd = await make_user(db, "fremd")
    r = await client.post("/places", headers=auth(ich),
                          json={"key": "zuhause", "lat": 50.0, "lon": 10.0})
    pid = r.json()["id"]

    assert (await client.get("/places", headers=auth(fremd))).json() == []
    assert (await client.delete(f"/places/{pid}", headers=auth(fremd))).status_code == 404


async def test_point_delete_zieht_den_counter_nach(client, db):
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy", settings={"min_distance_m": 0, "min_interval_s": 0})
    tok = await _token(client, ich)
    await client.post(f"/ingest/{tok}", json={"lat": 50.08, "lon": 10.56})
    await client.post(f"/ingest/{tok}", json={"lat": 50.09, "lon": 10.57})

    daten = (await client.get("/series/handy/points", headers=auth(ich))).json()
    assert daten["series"]["points"] == 2
    erster = daten["points"][0]["id"]

    assert (await client.delete(f"/series/handy/points/{erster}",
                                headers=auth(ich))).status_code == 204
    daten = (await client.get("/series/handy/points", headers=auth(ich))).json()
    assert daten["series"]["points"] == 1


async def test_umbenennen(client, db):
    """Namen aendern sich. Was dabei nicht geht, ist ein Schluessel, den es schon gibt."""
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy.alt")
    await _series(client, ich, "handy.belegt")

    r = await client.put("/series/handy.alt", headers=auth(ich),
                         json={"key": "tracker.neu", "name": "Neu"})
    assert r.status_code == 200 and r.json()["key"] == "tracker.neu"
    assert (await client.get("/series/handy.alt/points", headers=auth(ich))).status_code == 404
    assert (await client.get("/series/tracker.neu/points", headers=auth(ich))).status_code == 200

    r = await client.put("/series/tracker.neu", headers=auth(ich),
                         json={"key": "handy.belegt"})
    assert r.status_code == 409


async def test_umbenennen_behaelt_points_und_token(client, db):
    ich = await make_user(db, "ich")
    await _series(client, ich, "handy.alt", settings={"min_distance_m": 0, "min_interval_s": 0})
    tok = await _token(client, ich, "handy.alt")
    await client.post(f"/ingest/{tok}", json={"lat": 50.08, "lon": 10.56})

    await client.put("/series/handy.alt", headers=auth(ich), json={"key": "tracker.neu"})

    daten = (await client.get("/series/tracker.neu/points", headers=auth(ich))).json()
    assert len(daten["points"]) == 1
    # Das Token haengt an der Reihe, nicht am Schluessel — das Geraet meldet weiter.
    r = await client.post(f"/ingest/{tok}", json={"lat": 50.09, "lon": 10.57})
    assert r.status_code == 202 and r.json()["accepted"] == 1
