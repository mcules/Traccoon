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
