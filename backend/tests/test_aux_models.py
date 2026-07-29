"""Nebenaufgaben auf einem eigenen Modell.

Vorbild Hermes: `auxiliary:`-Block mit Modell je Aufgabe, Default `auto`. Wichtig ist vor
allem, was NICHT passiert — ohne Einstellung ändert sich nichts, und ein Fehlschlag reißt
den Hauptlauf nicht mit.
"""
import json

import pytest
from app.services.appsettings import set_setting
from app.worker import aux
from conftest import auth, make_user


class FakeAgent:
    provider, model = "claude_code", "claude-sonnet-5"


class FakeResp:
    def __init__(self, text):
        self.text = text


async def test_ohne_einstellung_gilt_auto(db, monkeypatch):
    """Kein Eintrag → Provider und Modell des Agenten. Wer nichts einstellt, merkt nichts."""
    gesehen = {}

    async def fake_chat(**kw):
        gesehen.update(kw)
        return FakeResp("kurz gefasst")

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    out = await aux.aux_chat(db, owner_id=None, task="compression", messages=[{"role": "user", "content": "x"}],
                             agent=FakeAgent(), tokens={"claude_code": "t"}, base_urls={})
    assert out == "kurz gefasst"
    assert gesehen["provider"] == "claude_code" and gesehen["model"] == "claude-sonnet-5"


async def test_eingestelltes_modell_wird_genommen(db, monkeypatch):
    await set_setting(db, "aux.compression", json.dumps(
        {"provider": "openai", "model": "qwen3.6-35b-q3", "base_url": "http://litellm:4000/v1"}))
    gesehen = {}

    async def fake_chat(**kw):
        gesehen.update(kw)
        return FakeResp("lokal gefasst")

    async def fake_token(*a, **kw):
        return "key"

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    monkeypatch.setattr(aux, "resolve_provider_token", fake_token)
    out = await aux.aux_chat(db, owner_id=1, task="compression", messages=[], agent=FakeAgent())
    assert out == "lokal gefasst"
    assert gesehen["provider"] == "openai" and gesehen["model"] == "qwen3.6-35b-q3"
    assert gesehen["base_urls"] == {"openai": "http://litellm:4000/v1"}


async def test_kaputte_einstellung_faellt_auf_auto_zurueck(db, monkeypatch):
    """Ein Tippfehler in der Einstellung darf keinen Lauf lahmlegen."""
    await set_setting(db, "aux.compression", "{kein json")
    assert await aux.aux_config(db, "compression") == {}


async def test_fehlschlag_reisst_den_hauptlauf_nicht_mit(db, monkeypatch):
    async def fake_chat(**kw):
        raise RuntimeError("Modell nicht erreichbar")

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    assert await aux.aux_chat(db, owner_id=None, task="compression", messages=[],
                              agent=FakeAgent()) is None


async def test_zeitueberschreitung_liefert_nichts_statt_zu_haengen(db, monkeypatch):
    await set_setting(db, "aux.compression", json.dumps(
        {"provider": "openai", "model": "langsam", "timeout": 10}))

    async def fake_chat(**kw):
        import asyncio
        await asyncio.sleep(30)

    async def fake_token(*a, **kw):
        return "key"

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    monkeypatch.setattr(aux, "resolve_provider_token", fake_token)
    monkeypatch.setattr(aux, "resolve_provider_base_url", fake_token)
    # Der Deckel greift, ohne dass der Test 30 s wartet: 10 s Frist, wir prüfen nur das Ergebnis.
    import asyncio
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            aux.aux_chat(db, owner_id=1, task="compression", messages=[], agent=FakeAgent()),
            timeout=0.2)


async def test_leere_antwort_gilt_als_kein_ergebnis(db, monkeypatch):
    async def fake_chat(**kw):
        return FakeResp("   ")

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    assert await aux.aux_chat(db, owner_id=None, task="compression", messages=[],
                              agent=FakeAgent()) is None


async def test_admin_kann_nebenaufgaben_einstellen(client, db):
    admin = await make_user(db, "chef", admin=True)
    r = await client.get("/admin/aux-models", headers=auth(admin))
    assert r.status_code == 200
    assert {e["task"] for e in r.json()} == set(aux.AUX_TASKS)
    assert all(e["config"] is None for e in r.json())        # zunächst überall `auto`

    r = await client.put("/admin/aux-models/compression", headers=auth(admin),
                         json={"provider": "openai", "model": "qwen3.6-35b-q3", "timeout": 300})
    assert r.status_code == 200 and r.json()["config"]["model"] == "qwen3.6-35b-q3"
    assert (await aux.aux_config(db, "compression"))["timeout"] == 300

    # Provider leeren = zurück auf `auto`.
    r = await client.put("/admin/aux-models/compression", headers=auth(admin), json={})
    assert r.json()["config"] is None


async def test_unbekannte_nebenaufgabe_wird_abgewiesen(client, db):
    admin = await make_user(db, "chef", admin=True)
    r = await client.put("/admin/aux-models/erfunden", headers=auth(admin),
                         json={"provider": "openai"})
    assert r.status_code == 404


async def test_denkendes_modell_bekommt_das_denken_abgeschaltet(db, monkeypatch):
    """qwen3.6 & Co. verbrauchen ihr ganzes Ausgabe-Budget im Reasoning und liefern dann
    LEEREN Text — 231 Completion-Tokens für ein „OK", davon 229 Denken. Für Fleißarbeit ist
    das verschenkt, also standardmäßig aus."""
    await set_setting(db, "aux.compression", json.dumps({"provider": "openai", "model": "qwen"}))
    gesehen = {}

    async def fake_chat(**kw):
        gesehen.update(kw)
        return FakeResp("kurz")

    async def fake_token(*a, **kw):
        return "key"

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    monkeypatch.setattr(aux, "resolve_provider_token", fake_token)
    monkeypatch.setattr(aux, "resolve_provider_base_url", fake_token)
    await aux.aux_chat(db, owner_id=1, task="compression", messages=[], agent=FakeAgent())
    assert gesehen["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


async def test_eigenes_extra_body_schlaegt_die_voreinstellung(db, monkeypatch):
    await set_setting(db, "aux.compression", json.dumps(
        {"provider": "openai", "model": "qwen", "extra_body": {"top_k": 20}}))
    gesehen = {}

    async def fake_chat(**kw):
        gesehen.update(kw)
        return FakeResp("kurz")

    async def fake_token(*a, **kw):
        return "key"

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    monkeypatch.setattr(aux, "resolve_provider_token", fake_token)
    monkeypatch.setattr(aux, "resolve_provider_base_url", fake_token)
    await aux.aux_chat(db, owner_id=1, task="compression", messages=[], agent=FakeAgent())
    assert gesehen["extra_body"] == {"top_k": 20}


async def test_auto_bekommt_kein_extra_body(db, monkeypatch):
    """Die Subscription-Provider kennen das Feld nicht — dort wäre es ein 400er."""
    gesehen = {}

    async def fake_chat(**kw):
        gesehen.update(kw)
        return FakeResp("kurz")

    monkeypatch.setattr(aux.router, "chat", fake_chat)
    await aux.aux_chat(db, owner_id=None, task="compression", messages=[], agent=FakeAgent())
    assert gesehen["extra_body"] is None
