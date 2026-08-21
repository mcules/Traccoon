"""Destinations: authentication, URL building and the resolution order.

Checking happens against a mock server (httpx MockTransport), so that every call lands there
and headers, URL and body can be looked at exactly, without a real network.
"""
import base64
import hashlib
import hmac as hmaclib
import json

import httpx
import pytest
from app.core.security import encrypt_secret
from app.models.destination import Destination
from app.models.enums import ProjectRole
from app.services import destinations as svc
from conftest import add_member, auth, make_project, make_user


def _mock(recording: list, status: int = 200, body: str = '{"ok":true}'):
    """httpx client that records every request and answers fixedly."""
    def handler(request: httpx.Request) -> httpx.Response:
        recording.append(request)
        return httpx.Response(status, content=body, headers={"Content-Type": "application/json"})
    transport = httpx.MockTransport(handler)

    class Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw.pop("verify", None)
            kw.pop("follow_redirects", None)
            super().__init__(*a, transport=transport, **kw)

    return Client


@pytest.fixture
def calls(monkeypatch):
    recording: list[httpx.Request] = []
    monkeypatch.setattr(svc.httpx, "AsyncClient", _mock(recording))
    return recording


async def _dest(db, **kw) -> Destination:
    d = Destination(name=kw.pop("name", "ziel"), base_url=kw.pop("base_url", "https://api.test/v1"),
                    **kw)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


def test_url_building():
    """Base URL plus path plus query, without doubled or missing slashes."""
    b = svc.build_url
    assert b("https://a.test/v1", "orders") == "https://a.test/v1/orders"
    assert b("https://a.test/v1/", "/orders") == "https://a.test/v1/orders"
    assert b("https://a.test/v1", "") == "https://a.test/v1"
    assert b("https://a.test", "/x", {"a": 1, "b": "z"}) == "https://a.test/x?a=1&b=z"
    # Bereits vorhandene Query bleibt erhalten.
    assert b("https://a.test/x?t=1", "", {"a": 2}) == "https://a.test/x?t=1&a=2"
    # Empty values fly out instead of landing in the URL as "None".
    assert b("https://a.test", "/x", {"a": None, "b": 1}) == "https://a.test/x?b=1"


async def test_basic_auth(db, calls):
    d = await _dest(db, auth_type="basic", username="anna", secret_enc=encrypt_secret("geheim"))
    await svc.call(db, d, method="GET", path="/me")
    header = calls[0].headers["authorization"]
    assert header == "Basic " + base64.b64encode(b"anna:geheim").decode()


async def test_bearer_auth(db, calls):
    d = await _dest(db, auth_type="bearer", secret_enc=encrypt_secret("t0k3n"))
    await svc.call(db, d, method="GET")
    assert calls[0].headers["authorization"] == "Bearer t0k3n"


async def test_api_key_in_header_and_query(db, calls):
    d = await _dest(db, auth_type="api_key", api_key_name="X-Key",
                    secret_enc=encrypt_secret("abc"))
    await svc.call(db, d, method="GET", path="/x")
    assert calls[0].headers["x-key"] == "abc"

    d.api_key_in = "query"
    d.api_key_name = "apikey"
    await db.commit()
    res = await svc.call(db, d, method="GET", path="/x", query={"a": 1})
    assert "apikey=abc" in str(calls[1].url)
    # The key must NOT stand in the returned URL.
    assert "abc" not in res["url"]


async def test_hmac_signature_without_a_prefix(db, calls):
    """Signature over the sent body; the prefix is configurable and empty by default (Hermes
    rejects a `sha256=` prefix)."""
    d = await _dest(db, auth_type="hmac", secret_enc=encrypt_secret("s3cr3t"),
                    hmac_header="X-Webhook-Signature")
    await svc.call(db, d, method="POST", body={"a": 1})
    sent = calls[0].content
    expected = hmaclib.new(b"s3cr3t", sent, hashlib.sha256).hexdigest()
    assert calls[0].headers["x-webhook-signature"] == expected
    assert json.loads(sent) == {"a": 1}

    d.hmac_prefix = "sha256="
    await db.commit()
    await svc.call(db, d, method="POST", body={"a": 1})
    assert calls[1].headers["x-webhook-signature"].startswith("sha256=")


async def test_oauth2_fetches_a_token_and_remembers_it(db, monkeypatch):
    """Client credentials: fetch the token once, take it from the cache afterwards."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "AT-1", "expires_in": 3600})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    class Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw.pop("verify", None)
            kw.pop("follow_redirects", None)
            super().__init__(*a, transport=transport, **kw)

    monkeypatch.setattr(svc.httpx, "AsyncClient", Client)

    d = await _dest(db, auth_type="oauth2_cc", secret_enc=encrypt_secret("cs"),
                    oauth_token_url="https://idp.test/token", oauth_client_id="cid",
                    oauth_scope="lesen")
    await svc.call(db, d, method="GET", path="/x")
    await svc.call(db, d, method="GET", path="/y")

    token_calls = [r for r in calls if r.url.path.endswith("/token")]
    assert len(token_calls) == 1, "the token was not cached"
    assert all(r.headers["authorization"] == "Bearer AT-1"
               for r in calls if not r.url.path.endswith("/token"))
    await db.refresh(d)
    assert d.oauth_expires_at is not None and d.oauth_token_enc


async def test_methods_and_body(db, calls):
    d = await _dest(db, auth_type="none")
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        await svc.call(db, d, method=verb, body={"x": 1})
    assert [r.method for r in calls] == ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    # With GET/HEAD/DELETE/OPTIONS the body is deliberately left out.
    without = {r.method: r.content for r in calls}
    assert without["GET"] == b"" and without["DELETE"] == b""
    assert json.loads(without["POST"]) == {"x": 1}

    with pytest.raises(ValueError):
        await svc.call(db, d, method="TRACE")


async def test_headers_of_the_destination_and_of_the_call(db, calls):
    d = await _dest(db, auth_type="none", default_headers={"X-Mandant": "a", "X-Fest": "1"})
    await svc.call(db, d, method="GET", headers={"X-Mandant": "b", "X-Extra": "2"})
    h = calls[0].headers
    assert h["x-mandant"] == "b"   # the call beats the destination
    assert h["x-fest"] == "1" and h["x-extra"] == "2"


async def test_resolution_project_before_user_before_global(db):
    user = await make_user(db, "anna")
    proj = await make_project(db, "TST", "Test")
    await _dest(db, name="crm", base_url="https://global.test")
    await _dest(db, name="crm", base_url="https://persoenlich.test", user_id=user.id)
    await _dest(db, name="crm", base_url="https://projekt.test", project_id=proj.id)

    g = await svc.resolve(db, "crm")
    u = await svc.resolve(db, "crm", owner_id=user.id)
    p = await svc.resolve(db, "crm", owner_id=user.id, project_id=proj.id)
    assert (g.base_url, u.base_url, p.base_url) == (
        "https://global.test", "https://persoenlich.test", "https://projekt.test")

    # Deactivated destinations are skipped.
    p.enabled = False
    await db.commit()
    assert (await svc.resolve(db, "crm", owner_id=user.id,
                              project_id=proj.id)).base_url == "https://persoenlich.test"
    assert await svc.resolve(db, "gibtsnicht") is None


async def test_agents_only_through_released_destinations(db, calls):
    await _dest(db, name="intern", auth_type="none")
    with pytest.raises(ValueError, match="not released for AI agents"):
        await svc.call_by_name(db, "intern", agents_only=True, method="GET")

    free = await svc.resolve(db, "intern")
    free.allow_agents = True
    await db.commit()
    res = await svc.call_by_name(db, "intern", agents_only=True, method="GET")
    assert res["ok"]


async def test_a_secret_is_never_returned(client, db):
    admin = await make_user(db, "chef", admin=True)
    r = await client.post("/destinations", headers=auth(admin), json={
        "name": "crm", "base_url": "https://api.test", "auth_type": "bearer", "token": "streng",
    })
    assert r.status_code == 201, r.text
    assert r.json()["has_secret"] is True
    assert "streng" not in r.text

    listing = await client.get("/destinations", headers=auth(admin))
    assert "streng" not in listing.text

    # An empty secret while editing leaves the old one standing.
    did = r.json()["id"]
    r2 = await client.put(f"/destinations/{did}", headers=auth(admin), json={"label": "CRM"})
    assert r2.json()["has_secret"] is True


async def test_a_system_wide_destination_is_admin_only(client, db):
    normal = await make_user(db, "otto")
    r = await client.post("/destinations", headers=auth(normal), json={
        "name": "extern", "base_url": "https://api.test"})
    assert r.status_code == 403

    # Personally they may.
    r = await client.post("/destinations", headers=auth(normal), json={
        "name": "extern", "base_url": "https://api.test", "user_id": normal.id})
    assert r.status_code == 201, r.text

    # Projektziel braucht maintainer+.
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, normal, ProjectRole.member)
    r = await client.post("/destinations", headers=auth(normal), json={
        "name": "p", "base_url": "https://api.test", "project_id": proj.id})
    assert r.status_code == 403


# ── Antwortgrenze je Ziel (TRA-31) ───────────────────────────────────────────

LARGE_ANSWER = json.dumps({"lage": "z" * 9000})


@pytest.fixture
def large_answer(monkeypatch):
    """A mock server that deliberately delivers more than the old flat cap let through."""
    monkeypatch.setattr(svc.httpx, "AsyncClient", _mock([], body=LARGE_ANSWER))
    return LARGE_ANSWER


async def test_the_default_response_limit_shortens(db, large_answer):
    """Without an entry of its own it stays at 4000 characters: existing destinations do not change."""
    d = await _dest(db, name="klein")
    assert d.max_response_chars == 4000
    res = await svc.call(db, d, method="GET")
    assert res["max_chars"] == 4000
    assert "text" not in res          # too long, so only as json, the full text suppressed


async def test_the_per_destination_response_limit_applies(db, large_answer):
    """A destination may let more through; otherwise an agent would plan on truncated JSON."""
    d = await _dest(db, name="gross", max_response_chars=40000)
    res = await svc.call(db, d, method="GET")
    assert res["max_chars"] == 40000
    assert res["text"] == large_answer


async def test_http_call_does_not_shorten_a_second_time(db, large_answer):
    """The agent tool must not revoke the permission of the destination."""
    from app.worker.tools_traccoon import call_traccoon_tool
    u = await make_user(db, "zielnutzer")
    await _dest(db, name="uniwar-bot", user_id=u.id, allow_agents=True,
                max_response_chars=40000)
    out = await call_traccoon_tool(db, u.id, "traccoon_http_call",
                                   {"destination": "uniwar-bot", "method": "GET"})
    assert "ABGESCHNITTEN" not in out
    assert len(out) > 9000


async def test_http_call_reports_the_cut(db, large_answer):
    """If it is truncated after all, the agent has to see it: a silent cut is worse than a
    short answer, because it plans on fragments."""
    from app.worker.tools_traccoon import call_traccoon_tool
    u = await make_user(db, "knappnutzer")
    await _dest(db, name="knapp", user_id=u.id, allow_agents=True, max_response_chars=500)
    out = await call_traccoon_tool(db, u.id, "traccoon_http_call",
                                   {"destination": "knapp", "method": "GET"})
    assert "ABGESCHNITTEN bei 500 Zeichen" in out
