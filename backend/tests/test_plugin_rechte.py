"""Was ein Plugin sehen darf — und wie es dazu kommt.

Ein Plugin ist fremder Code, der im Browser einer angemeldeten Person laeuft. Es kann sich
seine Rechte deshalb nicht selbst geben: Sein Manifest *fordert*, ein Admin *erlaubt*. Diese
Trennung ist die ganze Sicherheit des Systems, und sie hat drei Stellen, an denen sie
brechen koennte — das Einspielen, das Nachreichen einer neuen Fassung und das Setzen der
Haken.

Der Zaun um die ausgelieferte Seite gehoert dazu: Ohne `connect-src 'none'` haette ein
Plugin einen zweiten Weg ins Netz und die Bruecke waere Zierrat.
"""
import io
import json
import zipfile

from conftest import auth, make_user


def _zip(manifest: dict, dateien: dict[str, str] | None = None) -> bytes:
    """Ein Plugin-Zip im Speicher."""
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for pfad, inhalt in (dateien or {"index.html": "<h1>hi</h1>"}).items():
            zf.writestr(pfad, inhalt)
    return puffer.getvalue()


MANIFEST = {
    "slug": "probe", "name": "Probe", "version": "1.0.0", "entry": "index.html",
    "reads": ["series:number"],
    "contributions": [{"type": "page", "path": "", "label": "Probe"}],
}


async def _einspielen(client, admin, manifest=None, dateien=None):
    return await client.post(
        "/plugins", headers=auth(admin),
        files={"file": ("p.zip", _zip(manifest or MANIFEST, dateien), "application/zip")})


# ── Einspielen und Fordern ───────────────────────────────────────────────────

async def test_neues_plugin_bekommt_nichts_geschenkt(client, db):
    """Was das Manifest fordert, ist noch lange nicht erlaubt."""
    admin = await make_user(db, "chef", admin=True)
    r = await _einspielen(client, admin)
    assert r.status_code == 201, r.text

    (p,) = (await client.get("/plugins/alle", headers=auth(admin))).json()
    assert p["reads"] == ["series:number"]
    assert p["reads_granted"] == []


async def test_nur_admins_spielen_ein(client, db):
    mensch = await make_user(db, "mensch")
    r = await _einspielen(client, mensch)
    assert r.status_code == 403


# ── Freigeben ────────────────────────────────────────────────────────────────

async def test_freigabe_gilt_und_laesst_sich_zuruecknehmen(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _einspielen(client, admin)

    r = await client.put("/plugins/probe/rechte", headers=auth(admin),
                         json={"reads_granted": ["series:number"]})
    assert r.status_code == 200
    assert r.json()["reads_granted"] == ["series:number"]

    r = await client.put("/plugins/probe/rechte", headers=auth(admin),
                         json={"reads_granted": []})
    assert r.json()["reads_granted"] == []


async def test_ungefordertes_recht_laesst_sich_nicht_erlauben(client, db):
    """Sonst waere die Liste im Manifest nur Deko: Wer den Haken setzt, soll vorher gelesen
    haben, wonach gefragt wurde."""
    admin = await make_user(db, "chef", admin=True)
    await _einspielen(client, admin)

    r = await client.put("/plugins/probe/rechte", headers=auth(admin),
                         json={"reads_granted": ["series:location"]})
    assert r.status_code == 400
    assert r.json()["key"] == "err.right_not_requested"


async def test_neue_fassung_darf_sich_nicht_selbst_mehr_erlauben(client, db):
    """Der gefaehrlichste Weg: ein harmloses Plugin einspielen, freigeben lassen und in der
    naechsten Fassung stillschweigend mehr fordern."""
    admin = await make_user(db, "chef", admin=True)
    await _einspielen(client, admin)
    await client.put("/plugins/probe/rechte", headers=auth(admin),
                     json={"reads_granted": ["series:number"]})

    gierig = {**MANIFEST, "version": "2.0.0",
              "reads": ["series:number", "series:location", "series:text"]}
    assert (await _einspielen(client, admin, gierig)).status_code == 201

    (p,) = (await client.get("/plugins/alle", headers=auth(admin))).json()
    assert p["reads"] == ["series:number", "series:location", "series:text"]
    # Das Alte gilt weiter, das Neue faengt bei null an.
    assert p["reads_granted"] == ["series:number"]


async def test_weggefallenes_recht_verschwindet_auch_aus_der_freigabe(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _einspielen(client, admin)
    await client.put("/plugins/probe/rechte", headers=auth(admin),
                     json={"reads_granted": ["series:number"]})

    schlank = {**MANIFEST, "version": "2.0.0", "reads": []}
    await _einspielen(client, admin, schlank)

    (p,) = (await client.get("/plugins/alle", headers=auth(admin))).json()
    assert p["reads"] == [] and p["reads_granted"] == []


# ── Sichtbarkeit ─────────────────────────────────────────────────────────────

async def test_abgeschaltetes_plugin_ist_weder_sichtbar_noch_abrufbar(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _einspielen(client, admin)
    await client.put("/plugins/probe/rechte", headers=auth(admin), json={"enabled": False})

    assert (await client.get("/plugins", headers=auth(admin))).json() == []
    # Auch die Dateien: Ein abgeschaltetes Plugin soll nicht ueber die Adresse weiterleben.
    assert (await client.get("/plugins/probe/app/")).status_code == 404


async def test_nur_freigegebene_personen_sehen_es(client, db):
    admin = await make_user(db, "chef", admin=True)
    gast = await make_user(db, "gast")
    await _einspielen(client, admin)
    await client.put("/plugins/probe/rechte", headers=auth(admin),
                     json={"all_users": False, "allowed_user_ids": []})

    assert (await client.get("/plugins", headers=auth(gast))).json() == []

    await client.put("/plugins/probe/rechte", headers=auth(admin),
                     json={"allowed_user_ids": [gast.id]})
    assert [p["slug"] for p in (await client.get("/plugins", headers=auth(gast))).json()] \
        == ["probe"]


# ── Der Zaun um die Seite ────────────────────────────────────────────────────

async def test_ausgelieferte_seite_traegt_ihren_zaun(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _einspielen(client, admin)

    r = await client.get("/plugins/probe/app/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    # Der Kern: kein eigener Weg ins Netz, nichts ausser dem Erlaubten.
    assert "connect-src 'none'" in csp
    assert "default-src 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_manifest_oeffnet_nur_die_gemeldete_richtung(client, db):
    """Ein Kachelserver darf als Bild geladen werden — Skripte bleiben trotzdem zu."""
    admin = await make_user(db, "chef", admin=True)
    mit_karte = {**MANIFEST, "csp": {"img-src": ["https://tile.openstreetmap.org"],
                                     "script-src": ["https://boeser.example"]}}
    await _einspielen(client, admin, mit_karte)

    csp = (await client.get("/plugins/probe/app/")).headers["content-security-policy"]
    assert "https://tile.openstreetmap.org" in csp
    # script-src steht nicht in den erlaubten Richtungen und wird stillschweigend verworfen.
    assert "boeser.example" not in csp


async def test_dateitypen_stimmen(client, db):
    """Mit `nosniff` wendet der Browser ein Stylesheet nur an, wenn der Typ stimmt."""
    admin = await make_user(db, "chef", admin=True)
    await _einspielen(client, admin, dateien={
        "index.html": "<h1>hi</h1>", "stil.css": "body{}", "app.js": "1",
        "bild.svg": "<svg/>"})

    for pfad, typ in (("stil.css", "text/css"), ("app.js", "application/javascript"),
                      ("bild.svg", "image/svg+xml")):
        r = await client.get(f"/plugins/probe/app/{pfad}")
        assert r.headers["content-type"].startswith(typ), pfad
