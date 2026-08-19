"""Costs of a session: two numbers, and the one distinction Traccoon lacked.

`cost_usd_billed` is the billed amount from `cost_entries`; it stays what it was, even when
the catalog has changed since (`api/cost.py:148`). `cost_usd_estimated` computes the **step**
tokens against today's catalog. Both stand side by side, and neither overwrites the other.

The distinction it is about: a catalog entry with the price 0.00 means *priced and free* (the
local model), while no entry at all means *unknown*. Until now both gave the same 0.00 in the
display, and every gap in the catalog read like a gift.
"""
import datetime as dt

import pytest

from app.api import office as rt_api
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
    """See `test_office_api.py`: this wave does not register its router in `main.py`, because
    two waves hang on the file in parallel."""
    if not any(getattr(r, "path", "") == "/office/sessions" for r in api.routes):
        api.include_router(rt_api.router)


# ── Testdaten ────────────────────────────────────────────────────────────────

async def buehne(db):
    """User, project, ticket: the minimum for a session to be authorisable."""
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
    """A model turn as a step row, with the model that ACTUALLY answered."""
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
    r = await client.get(f"/office/sessions/issue/{issue.id}/cost", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()


# ── priced: the three states ─────────────────────────────────────────────────

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
    """`priced IS NULL` is the old row that never knew the distinction. It is resolved against
    the catalog at read time, and without an entry the 0.00 is a gap."""
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
    """The case of the local model: all prices 0.00, but there IS an entry. Exactly this
    distinction Traccoon could not make until now."""
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


# ── Aggregation over the tree ────────────────────────────────────────────────

async def test_by_agent_summiert_ueber_den_baum_inklusive_delegierter(client, db):
    """Two runs of the same agent (execution plus continuation) and one delegated sub-agent:
    all three belong to the same session and therefore in the same bill."""
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
    """The run switched to the fallback provider in the middle. Grouped by `run.model` that
    would be ONE row, and the wrong one: it would attribute the tokens of one model to the
    other."""
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
    """The catalog price changed after the billing. Both numbers stay: one says what it cost,
    the other what it would cost today."""
    user, _proj, issue = await buehne(db)
    run = await lauf(db, issue)
    await zug(db, run, provider="claude_code", model="sonnet", in_tok=MIO)
    await posten(db, run, priced=True, cost=1.0, in_tok=MIO)
    await katalog(db, "claude_code", "sonnet", ein=3.0, aus=15.0)   # more expensive today

    body = await kosten(client, user, issue)
    assert body["total"]["cost_usd_billed"] == 1.0
    assert body["total"]["cost_usd_estimated"] == 3.0
    zeile = body["by_agent"][0]
    assert zeile["cost_usd_billed"] == 1.0 and zeile["cost_usd_estimated"] == 3.0
    assert body["cost_partial"] is False


async def test_altlauf_ohne_schritt_tokens_faellt_auf_die_laufzeile_zurueck(client, db):
    """A run from before the instrumentation has no tokens on the steps but does have its sums
    on the run. Without this fallback the estimate would be 0 everywhere on the first day and
    the cost view useless."""
    user, _proj, issue = await buehne(db)
    await katalog(db, "claude_code", "sonnet", ein=3.0, aus=15.0)
    run = await lauf(db, issue, in_tok=MIO, out_tok=MIO)
    db.add(RunStep(run_id=run.id, seq=1, role="assistant", content="alt", created_at=NOW))
    await db.commit()

    body = await kosten(client, user, issue)
    assert body["total"]["cost_usd_estimated"] == 18.0
    assert body["by_model"][0]["unpriced"] is False


async def test_fremder_bekommt_404_auf_die_kosten(client, db):
    """Costs are project internals: the permission comes from the session, not from the path,
    and a stranger does not even learn that the session exists."""
    _user, _proj, issue = await buehne(db)
    fremd = await make_user(db, "fremd")
    await lauf(db, issue)

    r = await client.get(f"/office/sessions/issue/{issue.id}/cost", headers=auth(fremd))
    assert r.status_code == 404
