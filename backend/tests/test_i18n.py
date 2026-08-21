"""Languages and translation overrides.

The point of these endpoints is that a wrong label does not need a deployment. That only
holds if the state survives properly: a language stays after a rename, its texts stay when
it is switched off, and deleting one takes its texts with it instead of leaving orphans
that reappear under a recreated language.
"""
import pytest

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def test_shipped_languages_are_there_without_a_row(client, db):
    """German and English exist because their catalog ships, not because of a database row."""
    anna = await make_user(db, "anna")
    r = await client.get("/i18n/locales", headers=auth(anna))
    assert r.status_code == 200
    nach = {s["locale"]: s for s in r.json()}
    assert nach["de"]["name"] == "Deutsch" and nach["de"]["builtin"]
    assert nach["en"]["enabled"] is True


async def test_create_rename_disable_delete_a_language(client, db):
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)

    assert (await client.post("/i18n/locales", json={"locale": "FR", "name": "Französisch"},
                              headers=h)).status_code == 201
    # Creating twice is an error, not a silent overwrite: otherwise the name would be gone.
    assert (await client.post("/i18n/locales", json={"locale": "fr"}, headers=h)).status_code == 409

    await client.put("/i18n/fr/menu.start", json={"text": "Accueil"}, headers=h)
    assert (await client.put("/i18n/locales/fr", json={"name": "Français", "enabled": False},
                             headers=h)).status_code == 204

    entry = next(s for s in (await client.get("/i18n/locales", headers=h)).json()
                   if s["locale"] == "fr")
    assert entry["name"] == "Français"
    assert entry["enabled"] is False
    assert entry["own_texts"] == 1          # Abschalten wirft nichts weg
    assert entry["builtin"] is False

    assert (await client.delete("/i18n/locales/fr", headers=h)).status_code == 204
    after = (await client.get("/i18n/locales", headers=h)).json()
    assert not [s for s in after if s["locale"] == "fr"]
    assert (await client.get("/i18n/fr", headers=h)).json()["texts"] == {}


async def test_the_source_language_stays(client, db):
    """English is the fallback for every missing text. Off or gone it would leave keys on
    the screen, so both are refused."""
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)
    assert (await client.put("/i18n/locales/en", json={"enabled": False},
                             headers=h)).status_code == 400
    assert (await client.delete("/i18n/locales/en", headers=h)).status_code == 400


async def test_renaming_a_shipped_language_creates_a_row_only_then(client, db):
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)
    await client.put("/i18n/locales/en", json={"name": "British English"}, headers=h)
    nach = {s["locale"]: s for s in (await client.get("/i18n/locales", headers=h)).json()}
    assert nach["en"]["name"] == "British English"
    assert nach["en"]["builtin"] is True       # stays shipped, only named differently


async def test_only_an_admin_may_manage_languages(client, db):
    anna = await make_user(db, "anna")
    assert (await client.post("/i18n/locales", json={"locale": "it"},
                              headers=auth(anna))).status_code == 403
    assert (await client.delete("/i18n/locales/en", headers=auth(anna))).status_code == 403


async def test_empty_text_restores_the_shipped_version(client, db):
    admin = await make_user(db, "chef", admin=True)
    h = auth(admin)
    await client.put("/i18n/en/menu.start", json={"text": "Home sweet home"}, headers=h)
    assert (await client.get("/i18n/en", headers=h)).json()["texts"]["menu.start"]
    await client.put("/i18n/en/menu.start", json={"text": "  "}, headers=h)
    assert (await client.get("/i18n/en", headers=h)).json()["texts"] == {}


async def test_the_server_catalog_is_available_for_translation(client, db):
    """The server writes texts of its own (notifications, setup). Without this list the
    administration could not offer them, and they would stay German forever."""
    anna = await make_user(db, "anna")
    r = await client.get("/i18n/server-catalog", headers=auth(anna))
    assert r.status_code == 200
    texts = r.json()["texts"]
    assert texts["server.onboarding.project"] == "Projekt anlegen"
    assert all(k.startswith("server.") for k in texts)


async def test_server_text_in_the_readers_language(db):
    from app.services.i18n import tr, discard

    discard()
    assert await tr(db, "server.onboarding.project", "de") == "Projekt anlegen"
    assert await tr(db, "server.onboarding.project", "en") == "Create a project"
    # An unknown language falls back on German, not on the key: a key on the screen is worse
    # than a text in the wrong language.
    assert await tr(db, "server.onboarding.project", "fr") == "Projekt anlegen"
    assert await tr(db, "gibt.es.nicht", "de") == "gibt.es.nicht"


async def test_placeholders_are_filled(db):
    from app.services.i18n import tr

    text = await tr(db, "server.notify.job", "de", name="Nachtlauf")
    assert text == "Job: Nachtlauf"


async def test_an_admin_change_beats_the_shipped_text(client, db):
    from app.services.i18n import tr

    admin = await make_user(db, "chef", admin=True)
    await client.put("/i18n/en/server.onboarding.project",
                     json={"text": "Start a project"}, headers=auth(admin))
    # The cache is discarded on writing; otherwise the change would hang for 30 s.
    assert await tr(db, "server.onboarding.project", "en") == "Start a project"


async def test_onboarding_follows_the_users_language(client, db):
    anna = await make_user(db, "anna")
    anna.locale = "en"
    await db.commit()
    r = await client.get("/me/onboarding", headers=auth(anna))
    title = [s["title"] for s in r.json()["steps"]]
    assert "Create a project" in title
