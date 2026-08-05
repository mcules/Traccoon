"""Büro (office) — die Naht zwischen Datenbank und Ansicht.

Alles, was das Büro je zu sehen bekommt, geht durch `step_events`. Die Historie-API liest
`run_steps` und ruft sie; der Worker schreibt eine Zeile und ruft sie über `publish_step`.
Damit ist die *identische Form* beider Ströme gebaut und nicht bloß vereinbart — es gibt
keinen zweiten Ort, an dem ein Ereignis entstehen könnte, und also auch keinen, an dem
Live-Ansicht und Nachlese auseinanderlaufen könnten.

**Reihenfolge** ist `seq`, nie `ts`. `seq = run_steps.id * 4 + slot`: die Zeilen-ID ist
SERIAL, global monoton und exakt die Ankunftsreihenfolge — ein eigener Zähler wäre eine
zweite Wahrheit. Vier Slots je Zeile, damit eine Zeile mehr als ein Ereignis tragen kann:

    0  synthetisierter Vorgänger (nur bei Altzeilen: der `tool_start` zur `tool_result`)
    1  Hauptereignis
    2  abgeleiteter Begleiter (`usage`, `file_edit`, `agent_spawn`)
    3  reserviert — und Platz für die `run_end`-Grenze hinter der letzten Zeile

`2e9 * 4 < 2^53`, die Zahl bleibt in JavaScript exakt.

**`thinking` ist reserviert und wird nie emittiert.** Kein Provider-Adapter liefert
Denkblöcke; ein erfundenes Denken würde die Farben der Zeitleiste vergiften und Zeit
behaupten, die niemand gemessen hat. Die Art steht im Vertrag, damit sie später ohne
Versionssprung dazukommen kann — nicht, damit heute jemand sie füllt.

**Altdaten replayen.** Jede vor der Instrumentierung geschriebene Zeile hat `kind=''` und
wird beim Lesen aus `role` + `content` rekonstruiert. Ohne das startet das Büro leer.

**Nie geraten.** `ok` ist dreiwertig; wo der Erfolg nicht belegt ist, steht `None`. Ein
geratenes `True` wäre eine Lüge, die die Ansicht grün malt.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import PREFIX
from ..models.agents import RunStep

log = logging.getLogger("office")

# Version des Umschlags. Erhöhen, wenn ein Feld seine Bedeutung ändert — das Frontend
# verweigert dann die Anzeige, statt eine falsche Deutung zu zeichnen.
EVENT_VERSION = 1
# EIN globaler Kanal; Fan-out und Autorisierung macht die WS-Brücke. Ein Kanal je Projekt
# hätte projektlose Läufe nicht abbilden können und 40 Abos je Nutzer bedeutet.
CHANNEL = f"{PREFIX}office"
# Wie weit ein Ereignis „gerade eben" ist (Anschluss an den Live-Strom nach dem Snapshot).
LIVE_WINDOW_MS = 90_000
EVENT_CAP_DEFAULT = 4_000
EVENT_CAP_MAX = 20_000

# Slots je `run_steps`-Zeile (siehe Modul-Docstring).
SEQ_SLOTS = 4
SLOT_BEFORE = 0
SLOT_MAIN = 1
SLOT_DERIVED = 2

# Der vollständige Vertrag. `thinking` steht bewusst dabei und wird nie erzeugt.
KINDS = (
    "session_seen", "run_start", "user_message", "agent_text", "thinking", "usage",
    "tool_start", "tool_result", "file_edit", "agent_spawn", "run_end", "system",
)

# Kürzungsgrenzen — der Raum zeigt Vorschauen, nicht Volltexte.
ARGS_PREVIEW_CHARS = 400
RESULT_PREVIEW_CHARS = 2000

# Was die Laufzeit wirklich als Fehler zurückgibt (worker/runtime.py, mcp_client.py,
# tools_memory.py, codegraph.py). Alles andere ist unbekannt, nicht erfolgreich.
ERROR_PREFIXES = ("FEHLER:", "FEHLER ", "TOOL-FEHLER:", "FS-FEHLER:", "CHECK-FEHLER:", "❌", "⛔")

# Aus welchem Argument die Beschriftung eines Werkzeugs kommt. Explizite Tabelle statt
# Heuristik, und in derselben Schlüsselreihenfolge wie `worker/perms.resource_of` — damit
# der Raum dasselbe beschriftet, was im Berechtigungsdialog stand. MCP-Werkzeuge
# (`server__tool`) fehlen absichtlich: ihre Argumentnamen sind unbeschränkt, jede Regel
# dafür wäre geraten.
TOOL_TARGET_KEYS: dict[str, tuple[str, ...]] = {
    "fs_read": ("path",),
    "fs_write": ("path",),
    "fs_edit": ("path",),
    "fs_list": ("path",),
    "read_attachment": ("name",),
    "load_skill": ("key",),
    "screenshot": ("target",),
    "codegraph": ("query",),
    "delegate": ("role",),
    "traccoon_http_call": ("path", "url"),
}

# Werkzeuge, die eine Datei hinterlassen — nur sie bekommen den `file_edit`-Begleiter.
EDIT_TOOLS = ("fs_write", "fs_edit")

# Verdikt eines Laufs. `blocked`/`running` sind weder das eine noch das andere → None.
OK_STATUS = ("success", "planned")
FAIL_STATUS = ("failed", "loop_exhausted")

# Trenner zwischen Argumenten und Ergebnis in den Alt-Werkzeugzeilen
# (`worker/runtime.py`: f"args={…}\n→ {…}").
LEGACY_SPLIT = "\n→ "


# ── Kontext eines Laufs ──────────────────────────────────────────────────────

@dataclass
class RunCtx:
    """Alles, was ein Ereignis über seinen Lauf wissen muss — ohne DB-Zugriff.

    Der Worker hält eine Instanz über den ganzen Lauf und zählt `seq` hoch; die Lese-API
    baut sie einmal je Lauf aus der `runs`-Zeile. Kein Feld hier wird nachgeschlagen: die
    WS-Brücke autorisiert je Ereignis, und eine Query je Ereignis wäre nicht bezahlbar.
    """

    run_id: int
    project_id: int | None = None
    owner_id: int | None = None
    sid: str = ""
    agent: str = ""
    phase: str = ""
    provider: str = ""
    model: str = ""
    parent_run_id: int | None = None
    parent_tool_use_id: str | None = None
    spawn_depth: int = 0
    issue_key: str = ""
    continuation_index: int = 0
    task_id: str = ""
    seq: int = 0   # laufende RunStep.seq (der Worker zählt hier hoch)

    @classmethod
    def from_run(cls, run, *, issue_key: str = "") -> RunCtx:
        return cls(
            run_id=run.id,
            project_id=getattr(run, "project_id", None),
            owner_id=getattr(run, "owner_id", None),
            sid=session_id(run),
            agent=run.agent or "",
            phase=run.phase or "",
            provider=run.provider or "",
            model=run.model or "",
            parent_run_id=run.parent_run_id,
            parent_tool_use_id=getattr(run, "parent_tool_use_id", None),
            spawn_depth=int(getattr(run, "spawn_depth", 0) or 0),
            issue_key=issue_key,
            continuation_index=int(run.continuation_index or 0),
            task_id=run.task_id or "",
        )


def session_id(run) -> str:
    """Adresse des Raums, zu dem ein Lauf gehört.

    Ein Ticket ist ein Raum: Planungslauf, Ausführung, Fortsetzungen und alle Unteragenten
    gehören zusammen. Ein einzelner `Run` wäre zu klein (der Unteragent bekäme ein eigenes
    Büro), ein Projekt zu groß. Läufe ohne Ticket (Job, Assistent) adressieren über ihre
    Wurzel — mehr als einen Sprung nach oben kennt die Zeile nicht, tiefer verschachtelte
    Unterläufe landen deshalb am Zwischenlauf. Für die Anzeige reicht das, weil ohne Ticket
    ohnehin nur eine Kette entsteht.
    """
    if run.issue_id:
        return f"issue:{run.issue_id}"
    return f"run:{run.parent_run_id or run.id}"


def kind_from_role(role: str) -> str:
    """Ereignis-Art einer Altzeile aus ihrer `role`."""
    r = (role or "").strip().lower()
    if r == "assistant":
        return "agent_text"
    if r == "tool":
        return "tool_result"
    return "system"


def tool_target(tool: str, args: dict | None) -> str | None:
    """Womit das Werkzeug beschriftet wird — oder None, wenn nichts Sicheres da ist."""
    for key in TOOL_TARGET_KEYS.get(tool or "", ()):
        value = (args or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def tool_ok(result: str) -> bool | None:
    """False bei einem belegten Fehler, sonst None. Niemals ein geratenes True.

    Die Laufzeit meldet Erfolg nicht — sie gibt einfach das Ergebnis zurück. „Kein
    Fehlerpräfix" heißt deshalb nur „nicht als Fehler erkannt", und genau das ist `None`.
    Erst die Instrumentierung (Welle B) weiß, ob ein Aufruf durchlief, und schreibt `True`.
    """
    text = (result or "").lstrip()
    if not text:
        return None
    return False if text.startswith(ERROR_PREFIXES) else None


# ── Umschlag ────────────────────────────────────────────────────────────────

def _ts(value: dt.datetime | None) -> str:
    """ISO-8601 in UTC mit Millisekunden. Naive Zeitstempel gelten als UTC (SQLite liefert
    sie ohne Zone) — sonst verschöbe dieselbe Zeile je nach Datenbank um Stunden."""
    if value is None:
        value = dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    value = value.astimezone(dt.timezone.utc)
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


def _event(ctx: RunCtx, *, seq: int, ts: str, kind: str, **fields: Any) -> dict:
    """Der gemeinsame Umschlag. `project_id`/`owner_id` hängen an JEDEM Ereignis, damit die
    WS-Brücke ohne DB-Zugriff entscheiden kann, wer es sehen darf."""
    return {
        "v": EVENT_VERSION, "seq": seq, "ts": ts, "sid": ctx.sid,
        "project_id": ctx.project_id, "owner_id": ctx.owner_id,
        "run_id": ctx.run_id, "agent_id": f"run:{ctx.run_id}",
        "kind": kind, **fields,
    }


def _seq(step_id: int, slot: int) -> int:
    return int(step_id) * SEQ_SLOTS + slot


def _parse_args(raw: str) -> dict:
    """Argumente aus einer Vorschau lesen, soweit sie lesbar sind. Kein Fehler, wenn nicht —
    die Vorschau ist gekürzt und darf mitten im JSON abbrechen."""
    text = (raw or "").strip()
    # „args=" ist die Formatierung der alten Werkzeugzeile, kein Inhalt.
    if text.startswith("args="):
        text = text[len("args="):].strip()
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _args_of(step) -> dict:
    return _parse_args(step.content)


# ── Die Naht ────────────────────────────────────────────────────────────────

def step_events(step, ctx: RunCtx) -> list[dict]:
    """Eine `run_steps`-Zeile → die Ereignisse, die sie im Raum auslöst.

    Rein: keine DB, keine Uhr, keine Zufälligkeit. Nur so kann die Ansicht deterministisch
    zurückspulen und nur so liefern Historie und Live-Strom garantiert dasselbe.
    """
    kind = (getattr(step, "kind", "") or "").strip()
    if not kind:
        return _legacy_events(step, ctx)

    ts = _ts(step.created_at)
    main = _seq(step.id, SLOT_MAIN)
    derived = _seq(step.id, SLOT_DERIVED)
    tool = step.tool_name or ""
    out: list[dict] = []

    if kind == "run_start":
        out.append(_event(ctx, seq=main, ts=ts, kind="run_start",
                          agent=ctx.agent, phase=ctx.phase,
                          provider=ctx.provider, model=ctx.model,
                          parent_run_id=ctx.parent_run_id,
                          parent_tool_use_id=ctx.parent_tool_use_id,
                          spawn_depth=ctx.spawn_depth,
                          continuation_index=ctx.continuation_index,
                          task_id=ctx.task_id, issue_key=ctx.issue_key))

    elif kind == "user_message":
        # Die Quelle steht im `target` — eine eigene Spalte dafür wäre eine Spalte für ein
        # einziges Wort in einer von zehn Ereignisarten.
        out.append(_event(ctx, seq=main, ts=ts, kind="user_message",
                          source=(step.target or "system"), text=step.content or ""))

    elif kind == "agent_text":
        text = (step.content or "").strip()
        # „(Tool-Call)" ist der Platzhalter der Laufzeit für einen Zug ohne Text. Ungefiltert
        # sagte jeder Agent alle paar Sekunden „(Tool-Call)" in den Raum.
        if text and text != "(Tool-Call)":
            out.append(_event(ctx, seq=main, ts=ts, kind="agent_text", text=step.content))
        out.extend(_usage_event(step, ctx, seq=derived, ts=ts))

    elif kind == "usage":
        out.extend(_usage_event(step, ctx, seq=main, ts=ts))

    elif kind == "tool_start":
        args = _args_of(step)
        out.append(_event(ctx, seq=main, ts=ts, kind="tool_start",
                          tool=tool, target=(step.target or tool_target(tool, args)),
                          tool_use_id=step.tool_use_id,
                          args_preview=(step.content or "")[:ARGS_PREVIEW_CHARS]))
        # Der Spawn hängt am Werkzeugstart, nicht am Ergebnis: `delegate` wartet den
        # Unterlauf inline ab, die Ergebniszeile entsteht also erst bei dessen ENDE — der
        # Unteragent säße dann rückwirkend am Schreibtisch.
        if tool == "delegate":
            out.append(_event(ctx, seq=derived, ts=ts, kind="agent_spawn",
                              child_role=(step.target or args.get("role") or ""),
                              prompt=str(args.get("task") or ""),
                              tool_use_id=step.tool_use_id, background=False))

    elif kind == "tool_result":
        out.append(_event(ctx, seq=main, ts=ts, kind="tool_result",
                          tool=tool, tool_use_id=step.tool_use_id, ok=step.ok,
                          error=((step.content or "")[:RESULT_PREVIEW_CHARS]
                                 if step.ok is False else ""),
                          duration_ms=step.duration_ms,
                          result_preview=(step.content or "")[:RESULT_PREVIEW_CHARS]))
        # Nur bei belegtem Erfolg: ein fehlgeschlagenes fs_write hat nichts geschrieben,
        # und ein `None` weiß es nicht.
        if tool in EDIT_TOOLS and step.ok is True and step.target:
            out.append(_event(ctx, seq=derived, ts=ts, kind="file_edit", path=step.target))

    elif kind == "file_edit":
        out.append(_event(ctx, seq=main, ts=ts, kind="file_edit", path=step.target or ""))

    elif kind == "agent_spawn":
        args = _args_of(step)
        out.append(_event(ctx, seq=main, ts=ts, kind="agent_spawn",
                          child_role=(step.target or args.get("role") or ""),
                          prompt=str(args.get("task") or step.content or ""),
                          tool_use_id=step.tool_use_id, background=False))

    elif kind == "run_end":
        # Der Inhalt einer run_end-Zeile ist der Abschlussbericht als JSON — dieselben
        # Felder, die `run_boundary_events` aus der `runs`-Zeile zieht.
        out.append(_event(ctx, seq=main, ts=ts, kind="run_end", **_run_end_fields(_args_of(step))))

    else:
        # `system` und alles Unbekannte: als Systemzeile zeigen statt verschlucken.
        out.append(_event(ctx, seq=main, ts=ts, kind="system", text=step.content or ""))

    return out


def _usage_event(step, ctx: RunCtx, *, seq: int, ts: str) -> list[dict]:
    """Tokens eines Modellzugs. Kommt auch ohne Text — ein Zug, der nur Werkzeuge rief,
    hat trotzdem gekostet. Ohne Tokens gar kein Ereignis (sonst stünde die Zeitleiste voller
    Nullen). `cache_write_tokens` ist immer 0: kein Adapter meldet den Schreib-Anteil."""
    in_tok = int(getattr(step, "in_tokens", 0) or 0)
    out_tok = int(getattr(step, "out_tokens", 0) or 0)
    cache_read = int(getattr(step, "cache_read_tokens", 0) or 0)
    if not (in_tok or out_tok or cache_read):
        return []
    return [_event(ctx, seq=seq, ts=ts, kind="usage",
                   in_tokens=in_tok, out_tokens=out_tok, cache_read_tokens=cache_read,
                   cache_write_tokens=0,
                   provider=(step.provider or ctx.provider), model=(step.model or ctx.model))]


def _legacy_events(step, ctx: RunCtx) -> list[dict]:
    """Zeilen aus der Zeit vor der Instrumentierung (`kind=''`).

    Ohne diesen Pfad startet das Büro leer — die vorhandenen Läufe sind der einzige Grund,
    warum am ersten Tag überhaupt etwas zu sehen ist. Was hier fehlt, fehlt ehrlich:
    keine Dauer, keine Tokens je Zug, `ok` nur aus dem Fehlerpräfix.
    """
    ts = _ts(step.created_at)
    role = (step.role or "").strip().lower()

    if role == "assistant":
        text = (step.content or "").strip()
        if not text or text == "(Tool-Call)":
            return []
        return [_event(ctx, seq=_seq(step.id, SLOT_MAIN), ts=ts, kind="agent_text",
                       text=step.content)]

    if role == "tool":
        # Eine Altzeile ist Start UND Ergebnis in einem Feld: "args={…}\n→ …". Der Raum
        # braucht beide Hälften getrennt, sonst geht ein Werkzeug nie auf. Beide tragen
        # denselben Zeitstempel — die Dauer wurde damals nicht gemessen, und sie zu
        # schätzen hieße, Zeit zu erfinden.
        raw = step.content or ""
        args_text, _, result = raw.partition(LEGACY_SPLIT)
        args = _parse_args(args_text)
        # Das „args="-Präfix ist Formatierung der alten Zeile, kein Inhalt — im neuen Pfad
        # steht dort nacktes JSON, und beide sollen dieselbe Vorschau ergeben.
        if args_text.startswith("args="):
            args_text = args_text[len("args="):]
        tool = step.tool_name or ""
        use_id = f"legacy:{step.id}"
        return [
            _event(ctx, seq=_seq(step.id, SLOT_BEFORE), ts=ts, kind="tool_start",
                   tool=tool, target=tool_target(tool, args), tool_use_id=use_id,
                   args_preview=args_text[:ARGS_PREVIEW_CHARS]),
            _event(ctx, seq=_seq(step.id, SLOT_MAIN), ts=ts, kind="tool_result",
                   tool=tool, tool_use_id=use_id, ok=tool_ok(result),
                   error=(result[:RESULT_PREVIEW_CHARS] if tool_ok(result) is False else ""),
                   duration_ms=None, result_preview=result[:RESULT_PREVIEW_CHARS]),
        ]

    return [_event(ctx, seq=_seq(step.id, SLOT_MAIN), ts=ts, kind="system",
                   text=step.content or "")]


def _run_end_fields(src: dict) -> dict:
    """Die Felder eines `run_end` — aus einem Mapping, damit `runs`-Zeile und JSON-Inhalt
    einer run_end-Zeile durch DIESELBE Stelle laufen und nicht auseinanderdriften können."""
    status = str(src.get("status") or "")
    ok: bool | None = True if status in OK_STATUS else False if status in FAIL_STATUS else None
    return {
        "ok": ok, "status": status, "blocker_kind": src.get("blocker_kind"),
        "summary": src.get("summary") or "", "error": src.get("error") or "",
        "iterations": int(src.get("iterations") or 0),
        "in_tokens": int(src.get("in_tokens") or 0),
        "out_tokens": int(src.get("out_tokens") or 0),
        "cache_read_tokens": int(src.get("cache_read_tokens") or 0),
        "cost_usd": float(src.get("cost_usd") or 0.0),
        "cost_priced": src.get("cost_priced"),
    }


def run_boundary_events(run, ctx: RunCtx, *, first_step_id: int | None,
                        last_step_id: int | None, cost_priced: bool | None = None) -> list[dict]:
    """`run_start`/`run_end` für Läufe, die keine eigenen Grenzzeilen haben (Altläufe).

    Die Grenzen setzen sich zwischen die Schritte statt daneben: `run_start` auf
    `first_step_id*4 - 1` (der Slot 0 der ersten Zeile bleibt für deren eigenen Vorgänger
    frei), `run_end` auf `last_step_id*4 + 3`. Damit bleibt die Ordnung eine einzige
    aufsteigende Zahlenreihe, ohne Sonderfall beim Sortieren.

    Ohne Schritte gibt es keine Ankerpunkte und also auch keine Grenzen — ein Lauf ohne
    eine einzige Zeile hat im Raum nichts zu zeigen.
    """
    out: list[dict] = []
    if first_step_id:
        out.append(_event(ctx, seq=_seq(first_step_id, 0) - 1, ts=_ts(run.started_at),
                          kind="run_start",
                          agent=ctx.agent, phase=ctx.phase,
                          provider=ctx.provider, model=ctx.model,
                          parent_run_id=ctx.parent_run_id,
                          parent_tool_use_id=ctx.parent_tool_use_id,
                          spawn_depth=ctx.spawn_depth,
                          continuation_index=ctx.continuation_index,
                          task_id=ctx.task_id, issue_key=ctx.issue_key))
    # Ein laufender Lauf hat den Raum noch nicht verlassen.
    if last_step_id and run.status and run.status != "running":
        out.append(_event(ctx, seq=_seq(last_step_id, SEQ_SLOTS - 1),
                          ts=_ts(run.finished_at or run.started_at), kind="run_end",
                          **_run_end_fields({
                              "status": run.status, "blocker_kind": getattr(run, "blocker_kind", None),
                              "summary": run.summary or run.last_text or "", "error": run.error or "",
                              "iterations": run.iterations, "in_tokens": run.input_tokens,
                              "out_tokens": run.output_tokens,
                              # Der Lauf führt keine gecachten Tokens getrennt — die stehen
                              # nur am CostEntry. Hier ehrlich 0 statt geschätzt.
                              "cache_read_tokens": 0, "cost_usd": run.cost_usd,
                              "cost_priced": cost_priced,
                          })))
    return out


def session_seen_event(ctx: RunCtx, *, title: str, project_key: str,
                       started_at: dt.datetime | None, seq: int) -> dict:
    """Kopfzeile eines Raums — sagt der Ansicht, dass es die Session gibt, bevor der erste
    Agent zur Tür hereinkommt."""
    return _event(ctx, seq=seq, ts=_ts(started_at), kind="session_seen",
                  title=title, issue_key=ctx.issue_key, project_key=project_key,
                  started_at=_ts(started_at))


# ── Schreiben und Senden ────────────────────────────────────────────────────

async def add_step(db: AsyncSession, ctx: RunCtx, *, role: str, kind: str, content: str = "",
                   tool: str | None = None, target: str | None = None,
                   tool_use_id: str | None = None, ok: bool | None = None,
                   duration_ms: int | None = None, in_tokens: int = 0, out_tokens: int = 0,
                   cache_read_tokens: int = 0, provider: str = "", model: str = "",
                   commit: bool = True) -> RunStep:
    """Eine Schrittzeile schreiben — der EINE Weg dorthin.

    Der Worker (`_add_step`) und `open_room` benutzen dieselbe Funktion, damit es keine
    Zeile geben kann, die die Ereignisfelder nicht trägt. `ctx.seq` ist der laufende
    Zähler des Laufs (`RunStep.seq`), nicht die Ereignis-Nummer — die kommt aus `id`.
    `commit=False` erlaubt es, mehrere Zeilen in EINER Transaktion zu setzen.
    """
    ctx.seq += 1
    step = RunStep(
        run_id=ctx.run_id, seq=ctx.seq, role=role, kind=kind, tool_name=tool,
        content=(content or "")[:8000], target=(target or None), tool_use_id=tool_use_id,
        ok=ok, duration_ms=duration_ms, in_tokens=in_tokens, out_tokens=out_tokens,
        cache_read_tokens=cache_read_tokens, provider=provider or None, model=model or None,
    )
    db.add(step)
    if commit:
        await db.commit()
    return step


async def publish_step(ctx: RunCtx, step) -> None:
    """Die Ereignisse einer Zeile in den Live-Kanal geben.

    Schluckt JEDEN Fehler: ein ausgefallener Redis darf keinen Agentenlauf töten — die
    Ansicht ist ein Zuschauer, kein Beteiligter. Der Redis-Zugriff wird erst im Rumpf
    importiert, damit der Test-Ersatz (`conftest.redis_stub`) greift; ein Import am
    Modulkopf hätte die echte Funktion festgenagelt.
    """
    try:
        from ..core.redis import get_redis
        client = get_redis()
        for event in step_events(step, ctx):
            await client.publish(CHANNEL, json.dumps(event, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — bewusst alles
        log.debug("Büro: Ereignis von Lauf %s nicht gesendet", ctx.run_id, exc_info=True)


async def open_room(db: AsyncSession, ctx: RunCtx, *, agent, mode: str, issue: dict) -> None:
    """Den Raum eröffnen: der Agent kommt herein, und es steht dabei, WARUM.

    Beide Zeilen gehen in EINER Transaktion raus — ein Agent, der ohne Auftrag am
    Schreibtisch sitzt, wäre ein Zustand, den es nicht geben soll, auch nicht für 20 ms.
    """
    # Der Name kommt aus der Agentendefinition, falls der Kontext ihn noch nicht kennt —
    # der Raum beschriftet den Schreibtisch damit.
    if not ctx.agent:
        ctx.agent = getattr(agent, "name", "") or getattr(agent, "role", "") or ""
    start = await add_step(db, ctx, role="system", kind="run_start", content=mode,
                           provider=ctx.provider, model=ctx.model, commit=False)
    auftrag = "\n\n".join(p for p in (
        str(issue.get("summary") or ""), str(issue.get("description") or "")[:2000]) if p)
    auftrag_step = await add_step(db, ctx, role="user", kind="user_message", target="ticket",
                                  content=auftrag, commit=False)
    await db.commit()
    # Erst nach dem Commit senden: vorher hat die Zeile keine `id` und damit keine `seq`.
    await publish_step(ctx, start)
    await publish_step(ctx, auftrag_step)


# ── Preise ──────────────────────────────────────────────────────────────────

class PriceTable:
    """Modellkatalog einmal laden, in Python bepreisen (Preise sind USD je 1M Tokens).

    Der Unterschied, der Traccoon heute fehlt: ein Katalogeintrag mit 0,00 heißt *bepreist
    und gratis* (lokales Modell), gar kein Eintrag heißt *unbekannt*. Beides ergab bisher
    dieselbe 0,00 in der Anzeige, und jede Katalog-Lücke sah aus wie ein Geschenk.
    """

    def __init__(self, rows) -> None:
        self._by_key = {(r.provider or "", r.model or ""): r for r in rows}

    @classmethod
    async def load(cls, db: AsyncSession) -> PriceTable:
        from ..models.ops import ProviderModel
        rows = (await db.execute(select(ProviderModel))).scalars().all()
        return cls(rows)

    def has(self, provider: str, model: str) -> bool:
        return (provider or "", model or "") in self._by_key

    def price(self, provider: str, model: str, *, in_tokens: int = 0, out_tokens: int = 0,
              cache_read_tokens: int = 0) -> tuple[float, bool]:
        """(Kosten in USD, bepreist?). Ohne Katalogeintrag `(0.0, False)` — die Null ist
        dann eine Lücke und der Aufrufer muss sie als solche ausweisen."""
        row = self._by_key.get((provider or "", model or ""))
        if row is None:
            return 0.0, False
        return ((in_tokens or 0) / 1e6 * (row.price_input or 0.0)
                + (out_tokens or 0) / 1e6 * (row.price_output or 0.0)
                + (cache_read_tokens or 0) / 1e6 * (row.price_cache_read or 0.0)), True
