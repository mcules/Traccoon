"""The contract of the office, pure, without a database.

This file secures the one seam: `step_events` supplies the history API AND the live
publisher. If its shape deviates, follow-up reading and the live view drift apart, and
rewinding shows something other than watching. That is why the exact key set is checked here
and not merely "contains": an additional field is just as much a breach of contract as a
missing one.
"""
import datetime as dt

import pytest

from app.models.agents import RunStep
from app.services.office import (
    ERROR_PREFIXES, EVENT_VERSION, RunCtx, run_boundary_events, session_seen_event,
    step_events, tool_ok, tool_target,
)

TS = dt.datetime(2026, 8, 5, 11, 22, 33, 412000, tzinfo=dt.timezone.utc)

# Envelope: on EVERY event, independently of the kind.
BASE_KEYS = {"v", "seq", "ts", "sid", "project_id", "owner_id", "run_id", "agent_id", "kind"}

# The kind-owned fields. This table IS the contract. *Adding* a kind is additive: an
# interface that does not know `deploy` ignores it and shows everything else. *Reinterpreting*
# a field is not: then `EVENT_VERSION` has to rise so that the view refuses rather than draws
# wrongly (`services/office.py` says the same and is the source).
FIELD_KEYS = {
    "session_seen": {"title", "issue_key", "project_key", "started_at"},
    "run_start": {"agent", "phase", "provider", "model", "parent_run_id",
                  "parent_tool_use_id", "spawn_depth", "continuation_index",
                  "task_id", "issue_key"},
    "user_message": {"source", "text"},
    "agent_text": {"text"},
    "usage": {"in_tokens", "out_tokens", "cache_read_tokens", "cache_write_tokens",
              "provider", "model"},
    "tool_start": {"tool", "target", "tool_use_id", "args_preview"},
    "tool_result": {"tool", "tool_use_id", "ok", "error", "duration_ms", "result_preview"},
    "file_edit": {"path"},
    "agent_spawn": {"child_role", "prompt", "tool_use_id", "background"},
    "run_end": {"ok", "status", "blocker_kind", "summary", "error", "iterations",
                "in_tokens", "out_tokens", "cache_read_tokens", "cost_usd", "cost_priced"},
    "system": {"text"},
    "deploy": {"deployment_id", "state", "target", "log_head"},
}


def ctx() -> RunCtx:
    return RunCtx(run_id=8871, project_id=27, owner_id=3, sid="issue:412", agent="dev",
                  phase="execute", provider="claude_code", model="sonnet",
                  parent_run_id=None, parent_tool_use_id=None, spawn_depth=0,
                  issue_key="TRA-412", continuation_index=0, task_id="t-1")


def mk(step_id: int, **kw) -> RunStep:
    """RunStep without a session: the column defaults only take hold at the flush, so the
    fields are None. Exactly that way they come out of an old row as well."""
    kw.setdefault("created_at", TS)
    kw.setdefault("run_id", 8871)
    step = RunStep(**kw)
    step.id = step_id
    return step


def keys_of(kind: str) -> set[str]:
    return BASE_KEYS | FIELD_KEYS[kind]


class RunZeile:
    """Minimale `runs`-Attrappe für die Lauf-Grenzen."""

    def __init__(self, **kw):
        self.__dict__.update({
            "id": 8871, "status": "success", "started_at": TS, "finished_at": TS,
            "summary": "fertig", "last_text": "", "error": "", "iterations": 7,
            "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.5,
            "blocker_kind": None, **kw,
        })


# ── Key sets per kind ────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,step", [
    ("run_start", mk(10, kind="run_start", role="system")),
    ("user_message", mk(11, kind="user_message", role="user", target="ticket",
                        content="Auftrag")),
    ("agent_text", mk(12, kind="agent_text", role="assistant", content="Ich lese das.")),
    ("usage", mk(13, kind="usage", role="assistant", in_tokens=5, out_tokens=6,
                 cache_read_tokens=7, provider="openai", model="gpt")),
    ("tool_start", mk(14, kind="tool_start", role="tool", tool_name="fs_read",
                      tool_use_id="tu-1", content='{"path": "a.py"}')),
    ("tool_result", mk(15, kind="tool_result", role="tool", tool_name="fs_read",
                       tool_use_id="tu-1", ok=True, duration_ms=42, content="Inhalt")),
    ("file_edit", mk(16, kind="file_edit", role="tool", target="a.py")),
    ("agent_spawn", mk(17, kind="agent_spawn", role="tool", tool_use_id="tu-2",
                       target="reviewer", content='{"role": "reviewer", "task": "prüfen"}')),
    ("run_end", mk(18, kind="run_end", role="system", content='{"status": "success"}')),
    ("system", mk(19, kind="system", role="system", content="Hinweis")),
    ("deploy", mk(40, kind="deploy", role="system", target="/opt/docker/stacks/traccoon",
                  content='{"deployment_id": 187, "state": "ok", "log_head": "gebaut"}')),
])
def test_schluesselmenge_je_kind(kind, step):
    events = step_events(step, ctx())
    haupt = [e for e in events if e["kind"] == kind]
    assert len(haupt) == 1, f"{kind}: exactly one main event expected, {events}"
    assert set(haupt[0]) == keys_of(kind)
    assert haupt[0]["v"] == EVENT_VERSION


def test_session_seen_schluesselmenge():
    ev = session_seen_event(ctx(), title="Bug im Login", project_key="TRA",
                            started_at=TS, seq=0)
    assert set(ev) == keys_of("session_seen")
    assert ev["v"] == EVENT_VERSION


def test_umschlag_traegt_projekt_und_owner():
    # The WS bridge authorises with that alone; if it is missing on an event, it has to look
    # up or (worse) let it through.
    for ev in step_events(mk(20, kind="system", role="system", content="x"), ctx()):
        assert ev["project_id"] == 27 and ev["owner_id"] == 3
        assert ev["agent_id"] == "run:8871" and ev["sid"] == "issue:412"


def test_ts_ist_iso_mit_millisekunden():
    ev = step_events(mk(21, kind="system", role="system", content="x"), ctx())[0]
    assert ev["ts"] == "2026-08-05T11:22:33.412Z"


# ── seq-Formel ───────────────────────────────────────────────────────────────

def test_seq_formel_id_mal_vier_plus_slot():
    step = mk(1234, kind="tool_start", role="tool", tool_name="delegate",
              tool_use_id="tu-9", content='{"role": "reviewer", "task": "prüfen"}')
    events = step_events(step, ctx())
    assert [e["seq"] for e in events] == [1234 * 4 + 1, 1234 * 4 + 2]


def test_seq_ueber_gemischte_zeilen_streng_monoton():
    zeilen = [
        mk(101, role="assistant", content="alt"),                      # Altzeile
        mk(102, role="tool", tool_name="fs_read", content="args={}\n→ ok"),
        mk(103, kind="agent_text", role="assistant", content="neu", in_tokens=10),
        mk(104, kind="tool_start", role="tool", tool_name="delegate", tool_use_id="t",
           content='{"role": "qa", "task": "x"}'),
        mk(105, kind="tool_result", role="tool", tool_name="fs_edit", ok=True,
           target="a.py", content="ok"),
    ]
    seqs = [e["seq"] for z in zeilen for e in step_events(z, ctx())]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


# ── Altdaten ─────────────────────────────────────────────────────────────────

def test_altzeile_werkzeug_wird_gespalten():
    step = mk(777, role="tool", tool_name="fs_read",
              content='args={"path": "app/main.py"}\n→ Zeile 1\nZeile 2')
    events = step_events(step, ctx())
    assert len(events) == 2
    start, ergebnis = events
    assert start["kind"] == "tool_start" and ergebnis["kind"] == "tool_result"
    assert start["seq"] < ergebnis["seq"] == 777 * 4 + 1
    # The same timestamp: the duration was not measured back then, and estimating it would
    # mean inventing time.
    assert start["ts"] == ergebnis["ts"]
    assert ergebnis["duration_ms"] is None
    assert start["tool_use_id"] == ergebnis["tool_use_id"] == "legacy:777"
    assert start["args_preview"] == '{"path": "app/main.py"}'
    assert start["target"] == "app/main.py"
    assert ergebnis["result_preview"] == "Zeile 1\nZeile 2"
    assert ergebnis["ok"] is None


def test_altzeile_werkzeug_mit_fehler_ist_belegt_rot():
    step = mk(778, role="tool", tool_name="fs_edit",
              content='args={"path": "a.py"}\n→ FEHLER: `old` ist leer.')
    _, ergebnis = step_events(step, ctx())
    assert ergebnis["ok"] is False and ergebnis["error"].startswith("FEHLER:")


def test_altzeile_assistent_ohne_text_bleibt_stumm():
    assert step_events(mk(779, role="assistant", content="(Tool-Call)"), ctx()) == []
    assert step_events(mk(780, role="assistant", content="   "), ctx()) == []


def test_altzeile_system():
    ev = step_events(mk(781, role="system", content="Provider-Fehler: 500"), ctx())[0]
    assert ev["kind"] == "system" and ev["text"] == "Provider-Fehler: 500"


# ── tool_ok / tool_target ────────────────────────────────────────────────────

@pytest.mark.parametrize("prefix", ERROR_PREFIXES)
def test_tool_ok_erkennt_jedes_bekannte_fehlerpraefix(prefix):
    assert tool_ok(f"{prefix} etwas ging schief") is False


def test_tool_ok_raet_niemals_erfolg():
    # No error mark means "not recognised as an error", not "succeeded".
    assert tool_ok("Datei geschrieben (42 Zeilen)") is None
    assert tool_ok("") is None
    assert tool_ok("✅ OK") is None


@pytest.mark.parametrize("tool,args,erwartet", [
    ("fs_edit", {"path": "app/main.py", "old": "a"}, "app/main.py"),
    ("fs_read", {"path": "  a.py  "}, "a.py"),
    ("delegate", {"role": "reviewer", "task": "prüfen"}, "reviewer"),
    ("traccoon_http_call", {"url": "https://api.example.com/x"}, "https://api.example.com/x"),
    ("read_attachment", {"name": "shot.png"}, "shot.png"),
    # MCP tool: the argument names are unrestricted, and every rule would be guessed.
    ("obsidian__obsidian_get_note", {"target": {"path": "x.md"}}, None),
    ("fs_read", {}, None),
])
def test_tool_target_tabelle(tool, args, erwartet):
    assert tool_target(tool, args) == erwartet


# ── Begleiter ────────────────────────────────────────────────────────────────

def test_file_edit_begleiter_nur_bei_belegtem_erfolg():
    for ok, erwartet in ((True, 1), (False, 0), (None, 0)):
        step = mk(30, kind="tool_result", role="tool", tool_name="fs_write",
                  target="a.py", ok=ok, content="…")
        begleiter = [e for e in step_events(step, ctx()) if e["kind"] == "file_edit"]
        assert len(begleiter) == erwartet, f"ok={ok}"


def test_file_edit_begleiter_nur_bei_schreibenden_werkzeugen():
    step = mk(31, kind="tool_result", role="tool", tool_name="fs_read",
              target="a.py", ok=True, content="…")
    assert [e["kind"] for e in step_events(step, ctx())] == ["tool_result"]


def test_agent_spawn_begleiter_nur_bei_delegate():
    step = mk(32, kind="tool_start", role="tool", tool_name="delegate", tool_use_id="tu-5",
              content='{"role": "reviewer", "task": "Bitte prüfen"}')
    events = step_events(step, ctx())
    assert [e["kind"] for e in events] == ["tool_start", "agent_spawn"]
    spawn = events[1]
    # Without the child run id: that is still unknown at the tool start. The link comes into
    # being over `parent_tool_use_id` in the `run_start` of the child.
    assert set(spawn) == keys_of("agent_spawn") and spawn["run_id"] == 8871
    assert spawn["child_role"] == "reviewer" and spawn["prompt"] == "Bitte prüfen"
    assert spawn["tool_use_id"] == "tu-5" and spawn["background"] is False

    andere = mk(33, kind="tool_start", role="tool", tool_name="fs_read", tool_use_id="tu-6",
                content='{"path": "a.py"}')
    assert [e["kind"] for e in step_events(andere, ctx())] == ["tool_start"]


def test_usage_kommt_auch_ohne_text_und_agent_text_nicht_ohne():
    stumm = mk(34, kind="agent_text", role="assistant", content="(Tool-Call)",
               in_tokens=1200, out_tokens=80, cache_read_tokens=5000)
    events = step_events(stumm, ctx())
    assert [e["kind"] for e in events] == ["usage"]
    assert events[0]["in_tokens"] == 1200 and events[0]["cache_write_tokens"] == 0

    leer = mk(35, kind="agent_text", role="assistant", content="")
    assert step_events(leer, ctx()) == []

    beides = mk(36, kind="agent_text", role="assistant", content="Fertig.", out_tokens=9)
    assert [e["kind"] for e in step_events(beides, ctx())] == ["agent_text", "usage"]


def test_usage_ohne_tokens_erzeugt_nichts():
    step = mk(37, kind="agent_text", role="assistant", content="Fertig.")
    assert [e["kind"] for e in step_events(step, ctx())] == ["agent_text"]


# ── Lauf-Grenzen ─────────────────────────────────────────────────────────────

def test_run_boundary_umklammert_die_schritte():
    schritte = [mk(200, role="assistant", content="los"),
                mk(240, role="tool", tool_name="fs_read", content="args={}\n→ ok")]
    schritt_seqs = [e["seq"] for s in schritte for e in step_events(s, ctx())]
    grenzen = run_boundary_events(RunZeile(), ctx(), first_step_id=200, last_step_id=240)
    start, ende = grenzen
    assert start["kind"] == "run_start" and ende["kind"] == "run_end"
    assert start["seq"] < min(schritt_seqs)
    assert ende["seq"] > max(schritt_seqs)
    assert set(start) == keys_of("run_start") and set(ende) == keys_of("run_end")


@pytest.mark.parametrize("status,ok", [
    ("success", True), ("planned", True),
    ("failed", False), ("loop_exhausted", False),
    ("blocked", None),
])
def test_run_end_verdikt(status, ok):
    ende = run_boundary_events(RunZeile(status=status), ctx(),
                               first_step_id=1, last_step_id=2)[-1]
    assert ende["kind"] == "run_end" and ende["ok"] is ok


def test_laufender_lauf_verlaesst_den_raum_nicht():
    grenzen = run_boundary_events(RunZeile(status="running", finished_at=None), ctx(),
                                  first_step_id=1, last_step_id=2)
    assert [e["kind"] for e in grenzen] == ["run_start"]


def test_lauf_ohne_schritte_hat_keine_grenzen():
    assert run_boundary_events(RunZeile(), ctx(),
                               first_step_id=None, last_step_id=None) == []
