"""Ziele: Authentifizierung, URL-Bau und Auflösungs-Reihenfolge.

Geprüft wird gegen einen Schein-Server (httpx MockTransport) — jeder Aufruf landet dort,
sodass sich Kopfzeilen, URL und Body exakt nachsehen lassen, ohne echtes Netz.
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


def _mock(aufzeichnung: list, status: int = 200, body: str = '{"ok":true}'):
    """httpx-Client, der jeden Request aufzeichnet und fest antwortet."""
    def handler(request: httpx.Request) -> httpx.Response:
        aufzeichnung.append(request)
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
    aufzeichnung: list[httpx.Request] = []
    monkeypatch.setattr(svc.httpx, "AsyncClient", _mock(aufzeichnung))
    return aufzeichnung


async def _dest(db, **kw) -> Destination:
    d = Destination(name=kw.pop("name", "ziel"), base_url=kw.pop("base_url", "https://api.test/v1"),
                    **kw)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


def test_url_bau():
    """Basis-URL + Pfad + Query — ohne doppelte oder fehlende Schrägstriche."""
    b = svc.build_url
    assert b("https://a.test/v1", "orders") == "https://a.test/v1/orders"
    assert b("https://a.test/v1/", "/orders") == "https://a.test/v1/orders"
    assert b("https://a.test/v1", "") == "https://a.test/v1"
    assert b("https://a.test", "/x", {"a": 1, "b": "z"}) == "https://a.test/x?a=1&b=z"
    # Bereits vorhandene Query bleibt erhalten.
    assert b("https://a.test/x?t=1", "", {"a": 2}) == "https://a.test/x?t=1&a=2"
    # Leere Werte fliegen raus, nicht als "None" in die URL.
    assert b("https://a.test", "/x", {"a": None, "b": 1}) == "https://a.test/x?b=1"


async def test_basic_auth(db, calls):
    d = await _dest(db, auth_type="basic", username="anna", secret_enc=encrypt_secret("geheim"))
    await svc.call(db, d, method="GET", path="/me")
    kopf = calls[0].headers["authorization"]
    assert kopf == "Basic " + base64.b64encode(b"anna:geheim").decode()


async def test_bearer_auth(db, calls):
    d = await _dest(db, auth_type="bearer", secret_enc=encrypt_secret("t0k3n"))
    await svc.call(db, d, method="GET")
    assert calls[0].headers["authorization"] == "Bearer t0k3n"


async def test_api_key_header_und_query(db, calls):
    d = await _dest(db, auth_type="api_key", api_key_name="X-Key",
                    secret_enc=encrypt_secret("abc"))
    await svc.call(db, d, method="GET", path="/x")
    assert calls[0].headers["x-key"] == "abc"

    d.api_key_in = "query"
    d.api_key_name = "apikey"
    await db.commit()
    res = await svc.call(db, d, method="GET", path="/x", query={"a": 1})
    assert "apikey=abc" in str(calls[1].url)
    # Der Schlüssel darf NICHT in der zurückgegebenen URL stehen.
    assert "abc" not in res["url"]


async def test_hmac_signatur_ohne_praefix(db, calls):
    """Signatur über den gesendeten Body; Präfix ist konfigurierbar und standardmäßig leer
    (Hermes weist ein „sha256="-Präfix ab)."""
    d = await _dest(db, auth_type="hmac", secret_enc=encrypt_secret("s3cr3t"),
                    hmac_header="X-Webhook-Signature")
    await svc.call(db, d, method="POST", body={"a": 1})
    gesendet = calls[0].content
    erwartet = hmaclib.new(b"s3cr3t", gesendet, hashlib.sha256).hexdigest()
    assert calls[0].headers["x-webhook-signature"] == erwartet
    assert json.loads(gesendet) == {"a": 1}

    d.hmac_prefix = "sha256="
    await db.commit()
    await svc.call(db, d, method="POST", body={"a": 1})
    assert calls[1].headers["x-webhook-signature"].startswith("sha256=")


async def test_oauth2_holt_token_und_merkt_ihn(db, monkeypatch):
    """Client Credentials: einmal Token holen, danach aus dem Zwischenspeicher."""
    aufrufe: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        aufrufe.append(request)
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

    token_aufrufe = [r for r in aufrufe if r.url.path.endswith("/token")]
    assert len(token_aufrufe) == 1, "Token wurde nicht zwischengespeichert"
    assert all(r.headers["authorization"] == "Bearer AT-1"
               for r in aufrufe if not r.url.path.endswith("/token"))
    await db.refresh(d)
    assert d.oauth_expires_at is not None and d.oauth_token_enc


async def test_methoden_und_body(db, calls):
    d = await _dest(db, auth_type="none")
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        await svc.call(db, d, method=verb, body={"x": 1})
    assert [r.method for r in calls] == ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    # Bei GET/HEAD/DELETE/OPTIONS wird der Body bewusst weggelassen.
    ohne = {r.method: r.content for r in calls}
    assert ohne["GET"] == b"" and ohne["DELETE"] == b""
    assert json.loads(ohne["POST"]) == {"x": 1}

    with pytest.raises(ValueError):
        await svc.call(db, d, method="TRACE")


async def test_kopfzeilen_des_ziels_und_des_aufrufs(db, calls):
    d = await _dest(db, auth_type="none", default_headers={"X-Mandant": "a", "X-Fest": "1"})
    await svc.call(db, d, method="GET", headers={"X-Mandant": "b", "X-Extra": "2"})
    h = calls[0].headers
    assert h["x-mandant"] == "b"   # Aufruf schlägt Ziel
    assert h["x-fest"] == "1" and h["x-extra"] == "2"


async def test_aufloesung_projekt_vor_nutzer_vor_global(db):
    nutzer = await make_user(db, "anna")
    proj = await make_project(db, "TST", "Test")
    await _dest(db, name="crm", base_url="https://global.test")
    await _dest(db, name="crm", base_url="https://persoenlich.test", user_id=nutzer.id)
    await _dest(db, name="crm", base_url="https://projekt.test", project_id=proj.id)

    g = await svc.resolve(db, "crm")
    u = await svc.resolve(db, "crm", owner_id=nutzer.id)
    p = await svc.resolve(db, "crm", owner_id=nutzer.id, project_id=proj.id)
    assert (g.base_url, u.base_url, p.base_url) == (
        "https://global.test", "https://persoenlich.test", "https://projekt.test")

    # Deaktivierte Ziele werden übersprungen.
    p.enabled = False
    await db.commit()
    assert (await svc.resolve(db, "crm", owner_id=nutzer.id,
                              project_id=proj.id)).base_url == "https://persoenlich.test"
    assert await svc.resolve(db, "gibtsnicht") is None


async def test_agenten_nur_ueber_freigegebene_ziele(db, calls):
    await _dest(db, name="intern", auth_type="none")
    with pytest.raises(ValueError, match="nicht für KI-Agenten"):
        await svc.call_by_name(db, "intern", agents_only=True, method="GET")

    frei = await svc.resolve(db, "intern")
    frei.allow_agents = True
    await db.commit()
    res = await svc.call_by_name(db, "intern", agents_only=True, method="GET")
    assert res["ok"]


async def test_geheimnis_wird_nie_zurueckgegeben(client, db):
    admin = await make_user(db, "chef", admin=True)
    r = await client.post("/destinations", headers=auth(admin), json={
        "name": "crm", "base_url": "https://api.test", "auth_type": "bearer", "token": "streng",
    })
    assert r.status_code == 201, r.text
    assert r.json()["has_secret"] is True
    assert "streng" not in r.text

    liste = await client.get("/destinations", headers=auth(admin))
    assert "streng" not in liste.text

    # Leeres Geheimnis beim Ändern lässt das alte stehen.
    did = r.json()["id"]
    r2 = await client.put(f"/destinations/{did}", headers=auth(admin), json={"label": "CRM"})
    assert r2.json()["has_secret"] is True


async def test_systemweites_ziel_nur_admin(client, db):
    normal = await make_user(db, "otto")
    r = await client.post("/destinations", headers=auth(normal), json={
        "name": "extern", "base_url": "https://api.test"})
    assert r.status_code == 403

    # Persönlich darf er.
    r = await client.post("/destinations", headers=auth(normal), json={
        "name": "extern", "base_url": "https://api.test", "user_id": normal.id})
    assert r.status_code == 201, r.text

    # Projektziel braucht maintainer+.
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, normal, ProjectRole.member)
    r = await client.post("/destinations", headers=auth(normal), json={
        "name": "p", "base_url": "https://api.test", "project_id": proj.id})
    assert r.status_code == 403
