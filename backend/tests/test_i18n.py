"""Languages and translation overrides.

The point of these endpoints is that a wrong label does not need a deployment. That only
holds if the state survives properly: a language stays after a rename, its texts stay when
it is switched off, and deleting one takes its texts with it instead of leaving orphans
that reappear under a recreated language.
"""
import pytest

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def test_ausgelieferte_sprachen_stehen_ohne_zeile_da(client, db):
    """German and English exist because their catalog ships, not because of a database row."""
    anna = await make_user(db, "anna")
    r = await client.get("/i18n/locales", headers=auth(anna))
    assert r.status_code == 200
    nach = {s["locale"]: s for s in r.json()}
    assert nach["de"]["name"] == "Deutsch" and nach["de"]["eingebaut"]
    assert nach["en"]["enabled"] is True


async def test_sprache_anlegen_umbenennen_abschalten_loeschen(client, db):
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)

    assert (await client.post("/i18n/locales", json={"locale": "FR", "name": "Französisch"},
                              headers=h)).status_code == 201
    # Doppelt anlegen ist ein Fehler, kein stilles Überschreiben: sonst wäre der Name weg.
    assert (await client.post("/i18n/locales", json={"locale": "fr"}, headers=h)).status_code == 409

    await client.put("/i18n/fr/menu.start", json={"text": "Accueil"}, headers=h)
    assert (await client.put("/i18n/locales/fr", json={"name": "Français", "enabled": False},
                             headers=h)).status_code == 204

    eintrag = next(s for s in (await client.get("/i18n/locales", headers=h)).json()
                   if s["locale"] == "fr")
    assert eintrag["name"] == "Français"
    assert eintrag["enabled"] is False
    assert eintrag["eigene_texte"] == 1          # Abschalten wirft nichts weg
    assert eintrag["eingebaut"] is False

    assert (await client.delete("/i18n/locales/fr", headers=h)).status_code == 204
    danach = (await client.get("/i18n/locales", headers=h)).json()
    assert not [s for s in danach if s["locale"] == "fr"]
    assert (await client.get("/i18n/fr", headers=h)).json()["texte"] == {}


async def test_quellsprache_bleibt(client, db):
    """German is the fallback for every missing text. Off or gone it would leave keys on
    the screen, so both are refused."""
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)
    assert (await client.put("/i18n/locales/de", json={"enabled": False},
                             headers=h)).status_code == 400
    assert (await client.delete("/i18n/locales/de", headers=h)).status_code == 400


async def test_umbenennen_der_ausgelieferten_sprache_legt_erst_dann_eine_zeile_an(client, db):
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)
    await client.put("/i18n/locales/en", json={"name": "British English"}, headers=h)
    nach = {s["locale"]: s for s in (await client.get("/i18n/locales", headers=h)).json()}
    assert nach["en"]["name"] == "British English"
    assert nach["en"]["eingebaut"] is True       # bleibt ausgeliefert, nur anders benannt


async def test_nur_admin_darf_sprachen_verwalten(client, db):
    anna = await make_user(db, "anna")
    assert (await client.post("/i18n/locales", json={"locale": "it"},
                              headers=auth(anna))).status_code == 403
    assert (await client.delete("/i18n/locales/en", headers=auth(anna))).status_code == 403


async def test_leerer_text_stellt_die_ausgelieferte_fassung_wieder_her(client, db):
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)
    await client.put("/i18n/en/menu.start", json={"text": "Home sweet home"}, headers=h)
    assert (await client.get("/i18n/en", headers=h)).json()["texte"]["menu.start"]
    await client.put("/i18n/en/menu.start", json={"text": "  "}, headers=h)
    assert (await client.get("/i18n/en", headers=h)).json()["texte"] == {}


async def test_server_katalog_steht_zur_uebersetzung(client, db):
    """Der Server schreibt eigene Texte (Benachrichtigungen, Einrichtung). Ohne diese Liste
    könnte die Verwaltung sie nicht anbieten, und sie blieben für immer deutsch."""
    anna = await make_user(db, "anna")
    r = await client.get("/i18n/server-katalog", headers=auth(anna))
    assert r.status_code == 200
    texte = r.json()["texte"]
    assert texte["server.onboarding.project"] == "Projekt anlegen"
    assert all(k.startswith("server.") for k in texte)


async def test_servertext_in_der_sprache_des_lesers(db):
    from app.services.i18n import tr, verwerfen

    verwerfen()
    assert await tr(db, "server.onboarding.project", "de") == "Projekt anlegen"
    assert await tr(db, "server.onboarding.project", "en") == "Create a project"
    # Unbekannte Sprache fällt auf Deutsch zurück, nicht auf den Schlüssel: ein Schlüssel
    # auf dem Bildschirm ist schlimmer als ein Text in der falschen Sprache.
    assert await tr(db, "server.onboarding.project", "fr") == "Projekt anlegen"
    assert await tr(db, "gibt.es.nicht", "de") == "gibt.es.nicht"


async def test_platzhalter_werden_gefuellt(db):
    from app.services.i18n import tr

    text = await tr(db, "server.notify.job", "de", name="Nachtlauf")
    assert text == "Job: Nachtlauf"


async def test_admin_aenderung_schlaegt_den_ausgelieferten_text(client, db):
    from app.services.i18n import tr

    admin = await make_user(db, "chef", admin=True)
    await client.put("/i18n/en/server.onboarding.project",
                     json={"text": "Start a project"}, headers=auth(admin))
    # Der Zwischenspeicher wird beim Schreiben verworfen, sonst hinge die Änderung 30 s fest.
    assert await tr(db, "server.onboarding.project", "en") == "Start a project"


async def test_onboarding_folgt_der_sprache_des_nutzers(client, db):
    anna = await make_user(db, "anna")
    anna.locale = "en"
    await db.commit()
    r = await client.get("/me/onboarding", headers=auth(anna))
    titel = [s["title"] for s in r.json()["steps"]]
    assert "Create a project" in titel
