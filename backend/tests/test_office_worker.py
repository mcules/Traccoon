"""What the worker leaves behind in the office (the instrumentation wave).

What is checked is the way from the model turn to the `run_steps` row, with a scripted
provider and without a real MCP server. The transformation from row to event is checked
elsewhere (`test_office_normalize`); here what matters is whether the rows carry the right
fields AT ALL and come into being in the right order.

The hard regressions of this wave stand explicitly as tests of their own: a rejected tool
must not leave an open start behind, a provider error must not lose tokens, and a fallback
has to be priced with the model that actually answered.
"""
import asyncio
from contextlib import asynccontextmanager

import pytest
from app.models.agents import CostEntry, Run, RunStep
from app.models.ops import ProviderModel
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.models.enums import StatusCategory
from app.services import office
from app.worker import runtime as rt
from app.worker.providers.base import ChatResponse, ProviderError, ToolCall
from conftest import make_project, make_user
from sqlalchemy import select


# ── Scaffolding ──────────────────────────────────────────────────────────────

def antwort(text: str = "", *, calls: list[ToolCall] | None = None, in_tok: int = 0,
            out_tok: int = 0, cache: int = 0, provider: str = "", model: str = "") -> ChatResponse:
    return ChatResponse(text=text, tool_calls=list(calls or []), raw={},
                        usage={"input_tokens": in_tok, "output_tokens": out_tok},
                        cache_read_tokens=cache, provider=provider, model=model)


def agentdef(**kw) -> rt.AgentDef:
    """An agent without abilities: no workspace, no memory, no skills. `learns=False` keeps
    the review out of the way; it is a model turn of its own."""
    d = dict(id=None, name="dev", role="dev", system_prompt="Du bist dev.",
             provider="claude_code", model="sonnet", token_name="", fallback=None,
             fallback_model="", fallback_token_name="", temperature=0.3, max_tokens=1024,
             max_iterations=6, can_code=False, can_read_code=False, can_delegate=False,
             web_search=False, allowed_tools=["*"], allowed_skills=[], autoload_skills=[],
             delegate_to=[], learns=False)
    d.update(kw)
    return rt.AgentDef(**d)


class _Mcp:
    def __init__(self, ergebnis: str = "ok", verzoegerung: float = 0.0):
        self.ergebnis, self.verzoegerung = ergebnis, verzoegerung

    async def list_tools(self):
        return []

    async def call(self, name, args):
        if self.verzoegerung:
            await asyncio.sleep(self.verzoegerung)
        return self.ergebnis


@pytest.fixture(autouse=True)
def kein_redis(monkeypatch):
    """`publish_step` swallows errors, but nobody should look for a real Redis in the first
    place. The replacement collects what was sent: the live stream is part of the seam."""
    import app.core.redis as redismod
    gesendet: list[tuple[str, str]] = []

    class _R:
        async def publish(self, kanal, daten):
            gesendet.append((kanal, daten))

    monkeypatch.setattr(redismod, "get_redis", lambda: _R())
    return gesendet


@pytest.fixture
def lauf(db, monkeypatch):
    """Starts `run_agent` against a script of provider answers.

    An entry in the script that IS an exception is raised instead of returned, which lets the
    provider error in the middle of the run be reproduced.
    """
    async def starte(skript, *, mcp: _Mcp | None = None, agent: rt.AgentDef | None = None,
                     issue: dict | None = None, project: dict | None = None, **kw):
        rest = list(skript)
        gesehen: list[dict] = []

        async def fake_chat(**call_kw):
            gesehen.append(call_kw)
            naechste = rest.pop(0) if rest else antwort("fertig")
            if isinstance(naechste, Exception):
                raise naechste
            return naechste

        @asynccontextmanager
        async def fake_session(*a, **k):
            yield mcp or _Mcp()

        monkeypatch.setattr(rt.router, "chat", fake_chat)
        monkeypatch.setattr(rt, "mcp_session", fake_session)
        ergebnis = await rt.run_agent(
            db=db, agent=agent or agentdef(),
            issue=issue if issue is not None else {"id": None, "key": "job-1",
                                                   "summary": "Tu was", "description": "Bitte.",
                                                   "plan": None},
            project=project if project is not None else {"id": None, "key": "",
                                                         "system_prompt": ""},
            mode="execute", **kw)
        return ergebnis, gesehen

    return starte


async def schritte(db, run_id: int | None = None) -> list[RunStep]:
    q = select(RunStep).order_by(RunStep.id)
    if run_id is not None:
        q = q.where(RunStep.run_id == run_id)
    return list((await db.execute(q)).scalars().all())


async def letzter_lauf(db) -> Run:
    return (await db.execute(select(Run).order_by(Run.id.desc()))).scalars().first()


def ereignisse(steps: list[RunStep], run: Run) -> list[dict]:
    ctx = office.RunCtx.from_run(run)
    out: list[dict] = []
    for s in steps:
        out += office.step_events(s, ctx)
    return out


async def ticket(db, projekt):
    """A real ticket: the run has to be able to hang off one."""
    typ = IssueType(project_id=projekt.id, name="Aufgabe")
    status = WorkflowStatus(project_id=projekt.id, name="To Do", category=StatusCategory.todo)
    db.add_all([typ, status, IssueCounter(project_id=projekt.id, last_number=0)])
    await db.commit()
    i = Issue(project_id=projekt.id, number=1, key=f"{projekt.key}-1", type_id=typ.id,
              status_id=status.id, summary="Tu was", description="Bitte.", reporter_id=1, rank="1")
    db.add(i)
    await db.commit()
    return i


# ── The basic case ───────────────────────────────────────────────────────────

async def test_lauf_schreibt_die_ereignisse_in_seq_reihenfolge(db, lauf):
    _, _ = await lauf([
        antwort("Ich schaue nach.", in_tok=100, out_tok=20, cache=7,
                calls=[ToolCall(id="t1", name="open_tasks", arguments={})]),
        antwort("fertig"),
    ])
    run = await letzter_lauf(db)
    kinds = [e["kind"] for e in ereignisse(await schritte(db), run)]
    assert kinds == ["run_start", "user_message", "agent_text", "usage",
                     "tool_start", "tool_result", "agent_text", "run_end"]
    seqs = [e["seq"] for e in ereignisse(await schritte(db), run)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_auftrag_steht_als_user_message_im_raum(db, lauf):
    await lauf([antwort("fertig")])
    run = await letzter_lauf(db)
    zeile = next(s for s in await schritte(db) if s.kind == "user_message")
    assert zeile.target == "ticket"
    assert "Tu was" in zeile.content and "Bitte." in zeile.content
    assert run.status == "success"


async def test_werkzeug_wird_geoeffnet_und_geschlossen(db, lauf):
    await lauf([
        antwort(calls=[ToolCall(id="t1", name="open_tasks", arguments={})]),
        antwort("fertig"),
    ])
    start, ende = [s for s in await schritte(db) if s.kind in ("tool_start", "tool_result")]
    assert start.kind == "tool_start" and start.tool_use_id == "t1" and start.tool_name == "open_tasks"
    assert ende.kind == "tool_result" and ende.tool_use_id == "t1" and ende.ok is True
    assert ende.duration_ms is not None and ende.duration_ms >= 0


async def test_reiner_werkzeugzug_sagt_nichts_im_raum(db, lauf):
    """Without text the turn stays a pure cost row; otherwise every agent would say
    "(Tool-Call)" every few seconds."""
    await lauf([
        antwort(calls=[ToolCall(id="t1", name="open_tasks", arguments={})], in_tok=50, out_tok=5),
        antwort("fertig"),
    ])
    run = await letzter_lauf(db)
    zug = (await schritte(db))[2]
    assert zug.kind == "usage" and zug.content == "(Tool-Call)"     # the content as before
    assert [e["kind"] for e in office.step_events(zug, office.RunCtx.from_run(run))] == ["usage"]


async def test_fehlerhaftes_werkzeug_ist_belegt_gescheitert(db, lauf):
    await lauf([
        antwort(calls=[ToolCall(id="t1", name="load_skill", arguments={"key": "gibtsnicht"})]),
        antwort("fertig"),
    ])
    ende = next(s for s in await schritte(db) if s.kind == "tool_result")
    assert ende.ok is False and ende.content.startswith("FEHLER:")
    assert ende.target == "gibtsnicht"      # the label from the table, not guessed


async def test_dauer_waechst_mit_dem_langsamen_werkzeug(db, lauf):
    await lauf([
        antwort(calls=[ToolCall(id="t1", name="langsames_tool", arguments={})]),
        antwort("fertig"),
    ], mcp=_Mcp(ergebnis="fertig gerechnet", verzoegerung=0.06))
    ende = next(s for s in await schritte(db) if s.kind == "tool_result")
    assert ende.duration_ms >= 40


# ── Regression guard: the gate before the tool start ─────────────────────────

async def test_abgelehntes_werkzeug_erzeugt_keinen_start(db, lauf):
    """The `deny` branch does `continue`. If the start stood before it, an agent would sit in
    the room typing forever on a tool that never comes back."""
    ergebnis, _ = await lauf([
        antwort(calls=[ToolCall(id="t1", name="obsidian__obsidian_write_note",
                                arguments={"path": "a.md"})]),
        antwort("fertig"),
    ], gate_on=True, permissions=[{"tool": "*", "resource": "*", "action": "deny"}])
    alle = await schritte(db)
    assert [s.kind for s in alle if s.kind in ("tool_start", "tool_result")] == []
    assert ergebnis.status == "done"


# ── Delegation ───────────────────────────────────────────────────────────────

async def test_delegation_verbindet_eltern_und_kind(db, lauf):
    async def loader(rolle):
        return agentdef(name="reviewer", role="reviewer")

    await lauf([
        antwort(calls=[ToolCall(id="d1", name="delegate",
                                arguments={"role": "reviewer", "task": "Bitte prüfen"})]),
        antwort("Unterauftrag erledigt."),      # the child run
        antwort("fertig"),                      # the parent run afterwards
    ], agent=agentdef(can_delegate=True, delegate_to=["reviewer"]), delegate_loader=loader,
        issue={"id": None, "key": "TST-1", "summary": "Tu was", "description": "Bitte.",
               "plan": None})

    laeufe = (await db.execute(select(Run).order_by(Run.id))).scalars().all()
    eltern, kind = laeufe[0], laeufe[1]
    assert kind.parent_run_id == eltern.id
    assert kind.parent_tool_use_id == "d1" and kind.spawn_depth == 1

    alle = await schritte(db)
    start = next(s for s in alle if s.kind == "tool_start" and s.tool_name == "delegate")
    kind_start = next(s for s in alle if s.run_id == kind.id and s.kind == "run_start")
    kind_ende = next(s for s in alle if s.run_id == kind.id and s.kind == "run_end")
    ergebnis = next(s for s in alle if s.kind == "tool_result" and s.tool_name == "delegate")
    # The arrival order IS the id order (SERIAL), and exactly that is what the room draws.
    assert start.id < kind_start.id < kind_ende.id < ergebnis.id
    assert start.target == "reviewer"


# ── Affiliation of the run ───────────────────────────────────────────────────

async def test_ticketlauf_traegt_projekt_und_owner(db, lauf):
    nutzer = await make_user(db, "anna")
    projekt = await make_project(db, "TST", "Test")
    i = await ticket(db, projekt)
    await lauf([antwort("fertig")],
               issue={"id": i.id, "key": i.key, "summary": i.summary,
                      "description": i.description, "plan": None},
               project={"id": projekt.id, "key": projekt.key, "system_prompt": ""},
               owner_id=nutzer.id)
    run = await letzter_lauf(db)
    assert run.project_id == projekt.id and run.owner_id == nutzer.id and run.issue_id == i.id


async def test_joblauf_hat_kein_projekt_aber_einen_menschen(db, lauf):
    nutzer = await make_user(db, "anna")
    await lauf([antwort("fertig")], owner_id=nutzer.id)
    run = await letzter_lauf(db)
    # Project-less is the normal case for job and assistant runs, not an error.
    assert run.project_id is None and run.owner_id == nutzer.id


# ── Tokens and costs ─────────────────────────────────────────────────────────

async def test_schritt_tokens_summieren_sich_zum_lauf(db, lauf):
    await lauf([
        antwort("Erster Zug.", in_tok=100, out_tok=10,
                calls=[ToolCall(id="t1", name="open_tasks", arguments={})]),
        antwort("fertig", in_tok=250, out_tok=40),
    ])
    run = await letzter_lauf(db)
    alle = await schritte(db)
    assert sum(s.in_tokens for s in alle) == run.input_tokens == 350
    assert sum(s.out_tokens for s in alle) == run.output_tokens == 50


async def test_provider_fehler_verliert_die_tokens_nicht(db, lauf):
    """Up to the error everything is paid for; until now the whole run fell out of the bill."""
    ergebnis, _ = await lauf([
        antwort("Erster Zug.", in_tok=500, out_tok=60,
                calls=[ToolCall(id="t1", name="open_tasks", arguments={})]),
        ProviderError("529 overloaded"),
    ])
    run = await letzter_lauf(db)
    assert ergebnis.status == "failed"
    assert run.input_tokens == 500 and run.output_tokens == 60
    eintraege = (await db.execute(select(CostEntry))).scalars().all()
    assert len(eintraege) == 1 and eintraege[0].input_tokens == 500
    # The run leaves the room in the error case as well.
    assert (await schritte(db))[-1].kind == "run_end"


async def test_ohne_katalogeintrag_ist_die_null_eine_luecke(db, lauf):
    await lauf([antwort("fertig", in_tok=1000, out_tok=100)])
    run = await letzter_lauf(db)
    eintrag = (await db.execute(select(CostEntry))).scalars().first()
    assert run.cost_usd == 0.0
    assert eintrag.priced is False and eintrag.cost_usd == 0.0


async def test_katalogeintrag_mit_preis_null_ist_bepreist(db, lauf):
    db.add(ProviderModel(provider="claude_code", model="sonnet", price_input=0.0,
                         price_output=0.0, price_cache_read=0.0))
    await db.commit()
    await lauf([antwort("fertig", in_tok=1000, out_tok=100)])
    eintrag = (await db.execute(select(CostEntry))).scalars().first()
    assert eintrag.priced is True and eintrag.cost_usd == 0.0


async def test_lauf_ohne_tokens_bekommt_eine_ausgeschriebene_null(db, lauf):
    await lauf([antwort("fertig")])
    run = await letzter_lauf(db)
    assert run.cost_usd == 0.0
    assert (await db.execute(select(CostEntry))).scalars().all() == []


async def test_fallback_landet_am_schritt_nicht_am_lauf(db, lauf):
    """The run is configured on claude_code; what answered was the fallback. Without that on
    the step the turn would be priced with the wrong model."""
    await lauf([antwort("fertig", in_tok=10, out_tok=2, provider="codex", model="gpt-5-codex")])
    run = await letzter_lauf(db)
    zug = next(s for s in await schritte(db) if s.kind == "agent_text")
    assert zug.provider == "codex" and zug.model == "gpt-5-codex"
    assert run.provider == "claude_code" and run.model == "sonnet"


# ── Abschluss ────────────────────────────────────────────────────────────────

async def test_run_end_traegt_den_abschlussbericht(db, lauf):
    await lauf([antwort("fertig", in_tok=10, out_tok=2)])
    run = await letzter_lauf(db)
    ende = (await schritte(db))[-1]
    assert ende.kind == "run_end"
    ereignis = office.step_events(ende, office.RunCtx.from_run(run))[0]
    assert ereignis["status"] == "success" and ereignis["ok"] is True
    assert ereignis["in_tokens"] == 10 and ereignis["out_tokens"] == 2
    assert ereignis["cost_priced"] is False      # no catalog entry in the test


async def test_blockierter_lauf_nennt_den_grund(db, lauf):
    ergebnis, _ = await lauf([
        antwort(calls=[ToolCall(id="t1", name="ask_human",
                                arguments={"question": "Welche Farbe?"})]),
    ])
    run = await letzter_lauf(db)
    assert ergebnis.status == "blocked" and run.blocker_kind == "ask_human"
    ende = (await schritte(db))[-1]
    assert ende.kind == "run_end"
    assert office.step_events(ende, office.RunCtx.from_run(run))[0]["blocker_kind"] == "ask_human"


async def test_ereignisse_gehen_auch_live_raus(db, lauf, kein_redis):
    await lauf([antwort("fertig")])
    kanaele = {k for k, _ in kein_redis}
    assert kanaele == {office.CHANNEL}
    assert len(kein_redis) >= 3      # run_start, user_message, agent_text, run_end
