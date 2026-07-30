"""Was der Lauf am Agenten liest, muss der Agent auch tragen.

Anlass: Die Kompaktierung las `agent.max_context_tokens`, das Feld gab es aber nur am
DB-Modell, nicht am AgentDef des Laufs. Jeder Lauf, der die Stelle erreichte, starb an einem
AttributeError — und der Fehlerzweig verdeckte ihn: dort hiess `log` das Schrittprotokoll
(eine Funktion), `log.exception(...)` sprengte den Handler. Gemeldet wurde deshalb
„'function' object has no attribute 'exception'", der eigentliche Fehler stand nirgends.
"""
import ast
import inspect
import logging
from pathlib import Path

from app.models.agents import AgentDefinition
from app.worker import runtime
from app.worker.runtime import AgentDef, agent_def_from_row


def test_agentdef_traegt_kontextgrenze():
    row = AgentDefinition(role="assistent", system_prompt="", provider="claude_code",
                          model="claude-sonnet-5", temperature=0.3, max_tokens=16384,
                          max_context_tokens=120_000, max_turns_planning=5,
                          max_turns_execution=40)
    d = agent_def_from_row(row, "execute")
    assert d.max_context_tokens == 120_000


def test_lauf_liest_nur_vorhandene_agentfelder():
    """Jedes `agent.<feld>` in runtime.py muss es am AgentDef geben."""
    quelle = Path(inspect.getfile(runtime)).read_text()
    felder = set(AgentDef.__dataclass_fields__) | {
        n for n, _ in inspect.getmembers(AgentDef, predicate=inspect.isfunction)}
    gelesen = {
        node.attr
        for node in ast.walk(ast.parse(quelle))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.value.id == "agent"
    }
    assert gelesen <= felder, f"AgentDef fehlt: {sorted(gelesen - felder)}"


def test_fehlerzweig_meldet_den_echten_fehler(caplog):
    """`log` im Lauf muss der Logger sein — sonst verschluckt der Handler die Ursache."""
    assert isinstance(runtime.log, logging.Logger)
    with caplog.at_level(logging.ERROR):
        runtime.log.exception("Probe")
    assert "Probe" in caplog.text


async def test_watchdog_meldet_stillstand_genau_einmal(monkeypatch, caplog):
    """Steht der Loop, hilft keine Coroutine mehr beim Melden — der Wächter ist ein Thread.

    Anlass: Der Worker stand über eine Stunde ohne eine einzige Logzeile; von außen sah der
    Container gesund aus.
    """
    from app.worker import __main__ as worker

    dumps = []
    monkeypatch.setattr(worker.faulthandler, "dump_traceback", lambda: dumps.append(1))
    monkeypatch.setattr(worker, "LOOP_STALL_SEC", 10.0)
    monkeypatch.setattr(worker, "_LETZTER_TICK", worker.time.monotonic() - 60)

    with caplog.at_level(logging.ERROR):
        gemeldet = worker.watchdog_pruefe(False)
    assert gemeldet and dumps == [1]
    assert "tickt seit" in caplog.text

    # Zweiter Durchgang bei anhaltendem Stillstand: kein zweiter Dump (kein Log-Fluten).
    assert worker.watchdog_pruefe(True) is True
    assert dumps == [1]

    # Loop läuft wieder → Entwarnung, Zustand zurück.
    worker._loop_tick()
    assert worker.watchdog_pruefe(True) is False


async def test_modellkatalog_traegt_kontext_und_tempo(db):
    """Bei lokalen Modellen ist der Preis 0 — die Wahl entscheidet sich an Fenster und Tempo."""
    from app.api.cost import PriceIn, list_models, upsert_model
    from app.models.user import User as _User
    from conftest import make_user

    admin = await make_user(db, "chef", admin=True)
    await upsert_model(PriceIn(provider="openai", model="qwen3.6-35b-q8",
                               display_name="Qwen3.6 35B q8", context_tokens=131072,
                               speed_tps=42.5), admin, db)
    row = next(r for r in await list_models(admin, db) if r["model"] == "qwen3.6-35b-q8")
    assert row["context_tokens"] == 131072 and row["speed_tps"] == 42.5
    assert row["price_input"] == 0.0        # lokal: kostet nichts, taugt trotzdem etwas
    assert isinstance(admin, _User)


async def test_modellabruf_ueberschreibt_gepflegte_namen_nicht(db, monkeypatch):
    """OpenAI-kompatible Endpoints geben als „Namen" die Modell-ID zurück — ohne Schutz
    hätte jeder Abruf einen von Hand vergebenen Anzeigenamen wieder plattgemacht."""
    from app.api import cost as cost_api
    from app.models.ops import ProviderModel
    from app.models.secrets import ProviderToken
    from app.core.security import encrypt_secret
    from conftest import make_user
    from sqlalchemy import select as _select

    admin = await make_user(db, "chef2", admin=True)
    db.add(ProviderToken(user_id=admin.id, provider="openai", name="local",
                         value_enc=encrypt_secret("k"), base_url="http://litellm/v1",
                         is_default=True))
    db.add(ProviderModel(provider="openai", model="qwen3.6-35b-q8",
                         display_name="Qwen3.6 35B q8 (lokal)"))
    db.add(ProviderModel(provider="openai", model="frisch", display_name="frisch"))
    await db.commit()

    async def fake_fetch(provider, token, base_url=None):
        return [("qwen3.6-35b-q8", "qwen3.6-35b-q8"), ("frisch", "Frisch benannt")]

    monkeypatch.setattr(cost_api, "_fetch_provider_models", fake_fetch)
    await cost_api.fetch_models(admin, db)

    rows = {r.model: r.display_name for r in
            (await db.execute(_select(ProviderModel))).scalars().all()}
    assert rows["qwen3.6-35b-q8"] == "Qwen3.6 35B q8 (lokal)"   # gepflegt → bleibt
    assert rows["frisch"] == "Frisch benannt"                    # war = Modell-ID → darf mit
