"""Kosten einer Session — zwei Zahlen, und die eine Unterscheidung, die Traccoon fehlte.

`cost_usd_billed` ist der abgerechnete Betrag aus `cost_entries`; er bleibt, was er war,
auch wenn der Katalog sich seither geändert hat (`api/cost.py:148`). `cost_usd_estimated`
rechnet die **Schritt**-Tokens gegen den heutigen Katalog. Beide stehen nebeneinander,
keine überschreibt die andere.

Die Unterscheidung, um die es geht: ein Katalogeintrag mit Preis 0,00 heißt *bepreist und
gratis* (das lokale Modell), gar kein Eintrag heißt *unbekannt*. Bisher ergab beides
dieselbe 0,00 in der Anzeige, und jede Lücke im Katalog las sich wie ein Geschenk.
"""
import datetime as dt

import pytest

from app.api import roundtable as rt_api
from app.main import api
from app.models.agents import CostEntry, Run, RunStep
from app.models.enums import ProjectRole, StatusCategory
from app.models.ops import ProviderModel
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from conftest import add_member, auth, make_project, make_user

NOW = dt.datetime.now(dt.timezone.utc)
MIO = 1_000_000


@pytest.fixture(autouse=True)
def router_registriert():
    """Siehe `test_roundtable_api.py`: Welle C registriert ihren Router nicht in
    `main.py`, weil zwei Wellen parallel an der Datei hängen."""
    if not any(getattr(r, "path", "") == "/roundtable/sessions" for r in api.routes):
        api.include_router(rt_api.router)


# ── Testdaten ────────────────────────────────────────────────────────────────

async def buehne(db):
    """Nutzer, Projekt, Ticket — das Minimum, damit eine Session autorisierbar ist."""
    user = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, user, ProjectRole.member)
    typ = IssueType(project_id=proj.id, name="Aufgabe")
    status = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo)
    db.add_all([typ, status, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key="AAA-1", type_id=typ.id,
                  status_id=status.id, summary="Tu was", reporter_id=user.id, rank="1")
    db.add(issue)
    await db.commit()
    return user, proj, issue


async def lauf(db, issue, *, agent="developer", parent=None, provider="claude_code",
               model="sonnet", in_tok=0, out_tok=0) -> Run:
    r = Run(issue_id=issue.id, project_id=issue.project_id, agent=agent, phase="execute",
            provider=provider, model=model, status="success",
            parent_run_id=parent.id if parent else None,
            spawn_depth=1 if parent else 0,
            input_tokens=in_tok, output_tokens=out_tok,
            started_at=NOW - dt.timedelta(minutes=5), finished_at=NOW)
    db.add(r)
    await db.commit()
    return r


async def zug(db, run, *, provider, model, in_tok=0, out_tok=0, cache=0, seq=1):
    """Ein Modellzug als Schrittzeile — mit dem Modell, das TATSÄCHLICH geantwortet hat."""
    db.add(RunStep(run_id=run.id, seq=seq, role="assistant", kind="agent_text",
                   content="…", provider=provider, model=model, in_tokens=in_tok,
                   out_tokens=out_tok, cache_read_tokens=cache, created_at=NOW))
    await db.commit()


async def posten(db, run, *, priced, cost=1.0, provider="claude_code", model="sonnet",
                 in_tok=0, out_tok=0, cache=0):
    db.add(CostEntry(run_id=run.id, project_id=run.project_id, issue_id=run.issue_id,
                     agent=run.agent, provider=provider, model=model, input_tokens=in_tok,
                     output_tokens=out_tok, cache_read_tokens=cache, cost_usd=cost,
                     priced=priced))
    await db.commit()


async def katalog(db, provider, model, *, ein=0.0, aus=0.0, cache=0.0):
    db.add(ProviderModel(provider=provider, model=model, display_name=model,
                         price_input=ein, price_output=aus, price_cache_read=cache))
    await db.commit()


async def kosten(client, user, issue):
    r = await client.get(f"/roundtable/sessions/issue/{issue.id}/cost", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


# ── priced: die drei Zustände ────────────────────────────────────────────────

async def test_bepreister_posten_ist_vollstaendig(client, db):
    user, _proj, issue = await buehne(db)
    run = await lauf(db, issue)
    await posten(db, run, priced=True, cost=2.41, in_tok=1000, out_tok=200)

    body = await kosten(client, user, issue)
    assert body["cost_partial"] is False
    assert body["by_agent"][0]["unpriced"] is False
    assert body["by_agent"][0]["unpriced_models"] == []
    assert body["total"]["cost_usd_billed"] == 2.41


async def test_altzeile_ohne_katalogeintrag_ist_eine_preisluecke(client, db):
    """`priced IS NULL` ist die Altzeile, die die Unterscheidung nie kannte. Zur Lesezeit
    gegen den Katalog aufgelöst — und ohne Eintrag ist die 0,00 eben eine Lücke."""
    user, _proj, issue = await buehne(db)
    run = await lauf(db, issue)
    await posten(db, run, priced=None, cost=0.0, provider="lokal", model="qwen3.6")

    body = await kosten(client, user, issue)
    assert body["cost_partial"] is True
    assert body["by_agent"][0]["unpriced"] is True
    assert body["by_agent"][0]["unpriced_models"] == ["lokal/qwen3.6"]


async def test_altzeile_mit_katalogeintrag_gilt_als_bepreist(client, db):
    user, _proj, issue = await buehne(db)
    await katalog(db, "lokal", "qwen3.6", ein=0.1, aus=0.4)
    run = await lauf(db, issue)
    await posten(db, run, priced=None, cost=0.0, provider="lokal", model="qwen3.6")

    body = await kosten(client, user, issue)
    assert body["cost_partial"] is False
    assert body["by_agent"][0]["unpriced"] is False


async def test_katalogeintrag_mit_preis_null_ist_gratis_nicht_unbekannt(client, db):
    """Der Fall des lokalen Modells: alle Preise 0,00, aber es GIBT einen Eintrag.
    Genau diese Unterscheidung konnte Traccoon bisher nicht treffen."""
    user, _proj, issue = await buehne(db)
    await katalog(db, "lokal", "qwen3.6", ein=0.0, aus=0.0, cache=0.0)
    run = await lauf(db, issue, provider="lokal", model="qwen3.6")
    await zug(db, run, provider="lokal", model="qwen3.6", in_tok=MIO, out_tok=MIO)
    await posten(db, run, priced=True, cost=0.0, provider="lokal", model="qwen3.6")

    body = await kosten(client, user, issue)
    assert body["cost_partial"] is False
    assert body["by_model"] == [{
        "provider": "lokal", "model": "qwen3.6", "in_tokens": MIO, "out_tokens": MIO,
        "cache_read_tokens": 0, "cost_usd": 0.0, "unpriced": False,
    }]
    assert body["total"]["cost_usd_estimated"] == 0.0


# ── Aggregation über den Baum ────────────────────────────────────────────────

async def test_by_agent_summiert_ueber_den_baum_inklusive_delegierter(client, db):
    """Zwei Läufe desselben Agenten (Ausführung + Fortsetzung) und ein delegierter
    Unteragent — alle drei gehören zur selben Session und damit in dieselbe Rechnung."""
    user, _proj, issue = await buehne(db)
    a1 = await lauf(db, issue, agent="developer")
    a2 = await lauf(db, issue, agent="developer")
    sub = await lauf(db, issue, agent="reviewer", parent=a2)
    await posten(db, a1, priced=True, cost=1.0)
    await posten(db, a2, priced=True, cost=0.5)
    await posten(db, sub, priced=True, cost=0.25)

    body = await kosten(client, user, issue)
    rows = {r["agent"]: r for r in body["by_agent"]}
    assert set(rows) == {"developer", "reviewer"}
    assert rows["developer"]["runs"] == 2
    assert rows["developer"]["run_ids"] == [a1.id, a2.id]
    assert rows["developer"]["cost_usd_billed"] == 1.5
    assert rows["reviewer"]["cost_usd_billed"] == 0.25
    assert body["total"]["cost_usd_billed"] == 1.75


async def test_by_model_gruppiert_nach_dem_modell_des_schritts(client, db):
    """Der Lauf ist mitten drin auf den Fallback-Provider gewechselt. Nach `run.model`
    gruppiert wäre das EINE Zeile — und die falsche: sie schriebe die Tokens des einen
    Modells dem anderen zu."""
    user, _proj, issue = await buehne(db)
    await katalog(db, "claude_code", "sonnet", ein=3.0, aus=15.0)
    await katalog(db, "openai", "gpt-x", ein=1.0, aus=4.0)
    run = await lauf(db, issue, provider="claude_code", model="sonnet")
    await zug(db, run, provider="claude_code", model="sonnet", in_tok=MIO, seq=1)
    await zug(db, run, provider="openai", model="gpt-x", in_tok=2 * MIO, seq=2)

    body = await kosten(client, user, issue)
    zeilen = {(r["provider"], r["model"]): r for r in body["by_model"]}
    assert set(zeilen) == {("claude_code", "sonnet"), ("openai", "gpt-x")}
    assert zeilen[("claude_code", "sonnet")]["cost_usd"] == 3.0
    assert zeilen[("openai", "gpt-x")]["cost_usd"] == 2.0
    assert body["total"]["cost_usd_estimated"] == 5.0
    assert body["total"]["in_tokens"] == 3 * MIO


async def test_abgerechnet_und_geschaetzt_stehen_nebeneinander(client, db):
    """Der Katalogpreis hat sich nach der Abrechnung geändert. Beide Zahlen bleiben —
    die eine sagt, was es gekostet hat, die andere, was es heute kosten würde."""
    user, _proj, issue = await buehne(db)
    run = await lauf(db, issue)
    await zug(db, run, provider="claude_code", model="sonnet", in_tok=MIO)
    await posten(db, run, priced=True, cost=1.0, in_tok=MIO)
    await katalog(db, "claude_code", "sonnet", ein=3.0, aus=15.0)   # heute teurer

    body = await kosten(client, user, issue)
    assert body["total"]["cost_usd_billed"] == 1.0
    assert body["total"]["cost_usd_estimated"] == 3.0
    zeile = body["by_agent"][0]
    assert zeile["cost_usd_billed"] == 1.0 and zeile["cost_usd_estimated"] == 3.0
    assert body["cost_partial"] is False


async def test_altlauf_ohne_schritt_tokens_faellt_auf_die_laufzeile_zurueck(client, db):
    """Ein Lauf von vor der Instrumentierung hat keine Tokens an den Schritten, wohl aber
    seine Summen am Lauf. Ohne diesen Rückfall wäre die Schätzung am ersten Tag überall 0
    und die Kostenansicht nutzlos."""
    user, _proj, issue = await buehne(db)
    await katalog(db, "claude_code", "sonnet", ein=3.0, aus=15.0)
    run = await lauf(db, issue, in_tok=MIO, out_tok=MIO)
    db.add(RunStep(run_id=run.id, seq=1, role="assistant", content="alt", created_at=NOW))
    await db.commit()

    body = await kosten(client, user, issue)
    assert body["total"]["cost_usd_estimated"] == 18.0
    assert body["by_model"][0]["unpriced"] is False


async def test_fremder_bekommt_404_auf_die_kosten(client, db):
    """Kosten sind Projektinterna — die Berechtigung kommt aus der Session, nicht aus
    dem Pfad, und ein Fremder erfährt nicht einmal, dass die Session existiert."""
    _user, _proj, issue = await buehne(db)
    fremd = await make_user(db, "fremd")
    await lauf(db, issue)

    r = await client.get(f"/roundtable/sessions/issue/{issue.id}/cost", headers=auth(fremd))
    assert r.status_code == 404
