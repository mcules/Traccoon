"""Der freigegebene Plan gehört in die Ausführung.

`_process` reichte `plan` an `run_agent` durch, benutzt wurde er nur im Planungsmodus — der
Entwickler arbeitete aus der Ticket-Beschreibung. Bei ABC-31 war die ein Symptombericht
(„finde die Ursache, werte `job_runs` aus"), während der freigegebene Plan die Ursache
längst benannte, mit Datei und Zeilennummer. Am 2026-08-07 kostete das drei Läufe und 155
Züge ohne eine Zeile Code: der Agent erarbeitete sich die fertige Analyse noch einmal
selbst. Die Planungsphase ist wertlos, wenn ihr Ergebnis die Ausführung nicht erreicht.
"""
from app.worker import runtime
from test_lifecycle_process import _projekt_mit_ticket


class _Agent:
    id = None
    role = name = "developer"
    system_prompt = ""
    provider, model, token_name = "claude_code", "claude-sonnet-5", ""
    fallback, fallback_model, fallback_token_name = None, "", ""
    temperature, max_tokens, max_iterations = 0.3, 16384, 80
    can_code = can_read_code = True
    can_delegate = web_search = False
    allowed_tools: list = []
    allowed_skills: list = []
    autoload_skills: list = []
    delegate_to: list = []
    learns = False
    max_context_tokens = None
    effort = ""


async def _nachrichten(db, monkeypatch, mode: str, plan: str) -> list[dict]:
    """Baut den Prompt bis zum ersten Modellzug und gibt die Nachrichten zurück."""
    _, proj, issue, _ = await _projekt_mit_ticket(db)
    gesehen: list[dict] = []

    async def fake_chat(**kw):
        gesehen.extend(kw["messages"])
        raise RuntimeError("stop")           # nach dem Prompt-Aufbau reicht es

    monkeypatch.setattr(runtime.router, "chat", fake_chat)
    try:
        await runtime.run_agent(
            db=db, agent=_Agent(),
            issue={"id": issue.id, "key": issue.key, "summary": "Job schlägt fehl",
                   "description": "## Symptom\nSeit dem 03.08. jeden Tag error.", "plan": plan},
            project={"id": proj.id, "key": proj.key, "system_prompt": "", "stack_dir": "",
                     "live_url": ""},
            mode=mode, permissions={}, ws_root="", gate_on=False, tokens={})
    except Exception:  # noqa: BLE001 — der Abbruch ist gewollt
        pass
    return gesehen


PLAN = "## Ursache\n`__main__.py:554` wertet `loop_exhausted` als Fehler.\n## Umsetzung\n1. …"


async def test_ausfuehrung_bekommt_den_plan(monkeypatch, db):
    texte = " ".join(m.get("content") or "" for m in await _nachrichten(db, monkeypatch, "execute", PLAN))

    assert "Freigegebener Umsetzungsplan" in texte
    assert "__main__.py:554" in texte, "der Plan selbst fehlt im Prompt"
    assert "Arbeite ihn ab" in texte


async def test_ohne_plan_kein_leerer_abschnitt(monkeypatch, db):
    texte = " ".join(m.get("content") or "" for m in await _nachrichten(db, monkeypatch, "execute", ""))

    assert "Freigegebener Umsetzungsplan" not in texte


async def test_planungslauf_behaelt_seine_eigene_formulierung(monkeypatch, db):
    """In der Planung wird der Plan überarbeitet, nicht abgearbeitet."""
    texte = " ".join(m.get("content") or "" for m in await _nachrichten(db, monkeypatch, "plan", PLAN))

    assert "Bestehender Plan" in texte
    assert "Freigegebener Umsetzungsplan" not in texte
