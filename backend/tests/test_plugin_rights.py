"""What a plugin may see — and how it comes to that.

A plugin is foreign code that runs in the browser of a logged-in person. It therefore cannot
give itself its rights: its manifest *demands*, an admin *allows*. This separation is the whole
security of the system, and it has three places where it could break — the install, the
submission of a new version and the setting of the
Haken.

The fence around the delivered page belongs to it: without `connect-src 'none'` a plugin would
have a second way into the network and the bridge would be decoration.
"""
import io
import json
import zipfile

from conftest import auth, make_user


def _zip(manifest: dict, files: dict[str, str] | None = None) -> bytes:
    """Ein Plugin-Zip im Speicher."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, content in (files or {"index.html": "<h1>hi</h1>"}).items():
            zf.writestr(path, content)
    return buffer.getvalue()


MANIFEST = {
    "slug": "probe", "name": "Probe", "version": "1.0.0", "entry": "index.html",
    "reads": ["series:number"],
    "contributions": [{"type": "page", "path": "", "label": "Probe"}],
}


async def _feed(client, admin, manifest=None, files=None):
    return await client.post(
        "/plugins", headers=auth(admin),
        files={"file": ("p.zip", _zip(manifest or MANIFEST, files), "application/zip")})


# ── Einspielen und Fordern ───────────────────────────────────────────────────

async def test_a_new_plugin_gets_nothing_for_free(client, db):
    """What the manifest demands is far from allowed."""
    admin = await make_user(db, "chef", admin=True)
    r = await _feed(client, admin)
    assert r.status_code == 201, r.text

    (p,) = (await client.get("/plugins/all", headers=auth(admin))).json()
    assert p["reads"] == ["series:number"]
    assert p["reads_granted"] == []


async def test_only_admins_install(client, db):
    person = await make_user(db, "mensch")
    r = await _feed(client, person)
    assert r.status_code == 403


# ── Freigeben ────────────────────────────────────────────────────────────────

async def test_a_grant_applies_and_can_be_withdrawn(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _feed(client, admin)

    r = await client.put("/plugins/probe/rights", headers=auth(admin),
                         json={"reads_granted": ["series:number"]})
    assert r.status_code == 200
    assert r.json()["reads_granted"] == ["series:number"]

    r = await client.put("/plugins/probe/rights", headers=auth(admin),
                         json={"reads_granted": []})
    assert r.json()["reads_granted"] == []


async def test_an_unrequested_right_cannot_be_granted(client, db):
    """Otherwise the list in the manifest would be mere decoration: whoever ticks the box should have read
    haben, wonach gefragt wurde."""
    admin = await make_user(db, "chef", admin=True)
    await _feed(client, admin)

    r = await client.put("/plugins/probe/rights", headers=auth(admin),
                         json={"reads_granted": ["series:location"]})
    assert r.status_code == 400
    assert r.json()["key"] == "err.right_not_requested"


async def test_a_new_version_may_not_grant_itself_more(client, db):
    """The most dangerous path: install a harmless plugin, get it released and in the
    naechsten Fassung stillschweigend mehr fordern."""
    admin = await make_user(db, "chef", admin=True)
    await _feed(client, admin)
    await client.put("/plugins/probe/rights", headers=auth(admin),
                     json={"reads_granted": ["series:number"]})

    greedy = {**MANIFEST, "version": "2.0.0",
              "reads": ["series:number", "series:location", "series:text"]}
    assert (await _feed(client, admin, greedy)).status_code == 201

    (p,) = (await client.get("/plugins/all", headers=auth(admin))).json()
    assert p["reads"] == ["series:number", "series:location", "series:text"]
    # The old one keeps applying, the new one starts at zero.
    assert p["reads_granted"] == ["series:number"]


async def test_a_dropped_right_also_disappears_from_the_grant(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _feed(client, admin)
    await client.put("/plugins/probe/rights", headers=auth(admin),
                     json={"reads_granted": ["series:number"]})

    lean = {**MANIFEST, "version": "2.0.0", "reads": []}
    await _feed(client, admin, lean)

    (p,) = (await client.get("/plugins/all", headers=auth(admin))).json()
    assert p["reads"] == [] and p["reads_granted"] == []


# ── Sichtbarkeit ─────────────────────────────────────────────────────────────

async def test_a_disabled_plugin_is_neither_visible_nor_fetchable(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _feed(client, admin)
    await client.put("/plugins/probe/rights", headers=auth(admin), json={"enabled": False})

    assert (await client.get("/plugins", headers=auth(admin))).json() == []
    # The files too: a disabled plugin must not live on through its address.
    assert (await client.get("/plugins/probe/app/")).status_code == 404


async def test_only_released_people_see_it(client, db):
    admin = await make_user(db, "chef", admin=True)
    guest = await make_user(db, "gast")
    await _feed(client, admin)
    await client.put("/plugins/probe/rights", headers=auth(admin),
                     json={"all_users": False, "allowed_user_ids": []})

    assert (await client.get("/plugins", headers=auth(guest))).json() == []

    await client.put("/plugins/probe/rights", headers=auth(admin),
                     json={"allowed_user_ids": [guest.id]})
    assert [p["slug"] for p in (await client.get("/plugins", headers=auth(guest))).json()] \
        == ["probe"]


# ── The fence around the page ───────────────────────────────────────────────

async def test_the_served_page_carries_its_fence(client, db):
    admin = await make_user(db, "chef", admin=True)
    await _feed(client, admin)

    r = await client.get("/plugins/probe/app/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    # The core: no way into the network of its own, nothing beyond what is allowed.
    assert "connect-src 'none'" in csp
    assert "default-src 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_the_manifest_opens_only_the_declared_direction(client, db):
    """Ein Kachelserver darf als Bild geladen werden — Skripte bleiben trotzdem zu."""
    admin = await make_user(db, "chef", admin=True)
    with_map = {**MANIFEST, "csp": {"img-src": ["https://tile.openstreetmap.org"],
                                     "script-src": ["https://boeser.example"]}}
    await _feed(client, admin, with_map)

    csp = (await client.get("/plugins/probe/app/")).headers["content-security-policy"]
    assert "https://tile.openstreetmap.org" in csp
    # script-src is not among the allowed directions and is silently discarded.
    assert "boeser.example" not in csp


async def test_the_file_types_are_right(client, db):
    """With `nosniff` the browser applies a stylesheet only when the type is right."""
    admin = await make_user(db, "chef", admin=True)
    await _feed(client, admin, files={
        "index.html": "<h1>hi</h1>", "stil.css": "body{}", "app.js": "1",
        "bild.svg": "<svg/>"})

    for path, kind in (("stil.css", "text/css"), ("app.js", "application/javascript"),
                      ("bild.svg", "image/svg+xml")):
        r = await client.get(f"/plugins/probe/app/{path}")
        assert r.headers["content-type"].startswith(kind), path
