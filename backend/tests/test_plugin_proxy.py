"""The plugin proxy speaks with the house's name and carries bytes.

The occasion was the map: OpenStreetMap answers a browser that sends no Referer with a
block tile, and a plugin frame (origin `null`) can never send one. Their policy leaves a
second door open, an identifiable User-Agent, and that is the proxy's. Tiles are bytes, so
the proxy has to carry those too, and a tile asked for twice in a minute should travel once.
"""
import base64
import io
import json
import zipfile

import httpx
import pytest
import app.api.plugins as plugins_api

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio

MANIFEST = {
    "slug": "karte", "name": "Karte", "version": "1.0.0", "entry": "index.html",
    "reads": [], "contributions": [{"type": "page", "path": "", "label": "Karte"}],
    "allowed_hosts": ["tiles.example.org"],
}


def _zip(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("index.html", "<h1>karte</h1>")
    return buffer.getvalue()


class _Client:
    """A stand-in for httpx.AsyncClient: remembers what was asked, answers what it was told."""
    calls: list = []
    answer: httpx.Response | None = None

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, content=None):
        _Client.calls.append((method, url, dict(headers or {})))
        return _Client.answer


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


@pytest.fixture
def proxy(monkeypatch):
    _Client.calls = []
    _Client.answer = httpx.Response(200, content=PNG, headers={
        "content-type": "image/png", "cache-control": "max-age=600"})
    plugins_api._CACHE.clear()
    monkeypatch.setattr(plugins_api.httpx, "AsyncClient", _Client)
    # A public address for the allowed host, without a DNS lookup in the test.
    monkeypatch.setattr(plugins_api.socket, "getaddrinfo",
                        lambda host, *a, **k: [(None, None, None, None, ("93.184.216.34", 0))])
    return _Client


async def _install(client, db):
    admin = await make_user(db, "chef", admin=True)
    r = await client.post("/plugins", headers=auth(admin),
                          files={"file": ("k.zip", _zip(MANIFEST), "application/zip")})
    assert r.status_code in (200, 201), r.text
    return admin


async def test_bytes_arrive_as_base64_and_the_door_carries_the_house_name(client, db, proxy):
    admin = await _install(client, db)
    r = await client.post("/plugins/karte/fetch", headers=auth(admin), json={
        "url": "https://tiles.example.org/1/2/3.png",
        "headers": {"User-Agent": "something the plugin made up"},
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["content_type"] == "image/png"
    assert base64.b64decode(out["body_base64"]) == PNG
    assert "body" not in out
    method, url, headers = proxy.calls[0]
    assert (method, url) == ("GET", "https://tiles.example.org/1/2/3.png")
    assert headers["User-Agent"] == plugins_api.PROXY_USER_AGENT, "the plugin's name is not the one at the door"


async def test_text_stays_text(client, db, proxy):
    admin = await _install(client, db)
    proxy.answer = httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})
    out = (await client.post("/plugins/karte/fetch", headers=auth(admin),
                             json={"url": "https://tiles.example.org/x.txt"})).json()
    assert out["body"] == "hello" and "body_base64" not in out


async def test_a_tile_asked_for_twice_travels_once(client, db, proxy):
    admin = await _install(client, db)
    url = "https://tiles.example.org/1/2/3.png"
    first = (await client.post("/plugins/karte/fetch", headers=auth(admin), json={"url": url})).json()
    second = (await client.post("/plugins/karte/fetch", headers=auth(admin), json={"url": url})).json()
    assert first == second
    assert len(proxy.calls) == 1, "max-age says the answer holds; the far side is not asked again"

    proxy.answer = httpx.Response(200, content=PNG, headers={
        "content-type": "image/png", "cache-control": "no-cache"})
    other = "https://tiles.example.org/4/5/6.png"
    await client.post("/plugins/karte/fetch", headers=auth(admin), json={"url": other})
    await client.post("/plugins/karte/fetch", headers=auth(admin), json={"url": other})
    assert len(proxy.calls) == 3, "no-cache is a request not to keep it, and it is honoured"


async def test_a_host_the_manifest_does_not_name_stays_closed(client, db, proxy):
    admin = await _install(client, db)
    r = await client.post("/plugins/karte/fetch", headers=auth(admin),
                          json={"url": "https://elsewhere.example.org/1.png"})
    assert r.status_code == 400
    assert not proxy.calls
