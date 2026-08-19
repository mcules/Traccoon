"""Build a flow from a description.

The editor is fair to anyone who already knows what a graph looks like. Anyone who does
not sits in front of a start and an end node and is supposed to assemble something from
twelve building blocks whose rules (which outlet, which parameter, which context path)
only become visible during validation. Templates help, but only while your case resembles
one of the four patterns.

Here you describe it in one sentence instead, and a model draws the graph. Drawing only,
on purpose: the draft lands on the canvas, not in the database. Saving and publishing stay
manual, because a flow nobody has looked at must not be able to run.

Two things make the result usable instead of merely plausible:

* The contract below is the same one `validate_graph` checks: outlet names, required
  fields, action names. The model does not guess, it gets the rules.
* Whatever it returns is validated at once. If validation finds errors, the model gets
  them back and fixes them (once). If anything is left, the draft still goes onto the
  canvas together with the errors, because an almost finished graph is worth more than an
  error message.
"""
from __future__ import annotations

import json
import logging
import os
import re

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import WorkflowSubjectKind
from ..worker.providers.router import router as llm_router
from ..worker.secrets import resolve_provider_token
from .workflow_engine import validate_graph

log = logging.getLogger("workflow_author")

DEFAULT_MODEL = os.getenv("DEFAULT_CLAUDE_MODEL", "claude-sonnet-4-5")
MAX_WERKZEUGE = 220          # prompt cap, the picker in the editor stays complete
SPALTE, ZEILE = 260, 130     # same grid the shipped graphs use

KONTRAKT = """\
Du zeichnest Abläufe für Traccoon als gerichteten Graphen. Antworte AUSSCHLIESSLICH mit
einem JSON-Objekt, ohne Fließtext und ohne Code-Zäune:

{"erklaerung": "1-3 Sätze, was der Ablauf tut", "nodes": [...], "edges": [...]}

Ein Knoten: {"id": "kurz_und_eindeutig", "type": "<typ>", "data": {"config": {...}}}
Eine Kante: {"id": "e1", "source": "<knoten>", "target": "<knoten>", "sourceHandle": "<ausgang oder weg>", "label": "optional"}
Positionen lässt du weg — die werden gesetzt.

REGELN (daran wird geprüft, ein Verstoß macht den Ablauf unbrauchbar):
- Genau EIN start-Knoten, mindestens ein end-Knoten, jedes Ende vom Start erreichbar.
- Jeder Knoten außer start hat eine eingehende, jeder außer end eine ausgehende Kante.
- Knoten mit festen Ausgängen brauchen für JEDEN eine Kante:
    decision  → je Zweig ein `handle`, dazu der Standard-Zweig (`default_handle`,
                der einer der Zweige sein MUSS)
    approval  → "approved" und "rejected"
    loop      → "element" (Schleifenkörper) und "fertig"; der Körper führt per Kante
                OHNE sourceHandle zur Schleife zurück
    subflow   → "completed" und "failed"
    auto_action → normal ohne sourceHandle; zusätzlich optional "error"
- Knotentypen und ihre Konfiguration:
    start       {"label", "trigger": {"kind": "webhook"|"ereignis", "event": "...",
                 "sample": {...Beispiel-Nutzlast...}}}  — ohne trigger: manueller Start/Job
    end         {"label", "outcome": "completed"|"failed"}
    auto_action {"label", "action": {"action": "<name>", "params": {...}},
                 "wiederholungen": 3, "warte_sek": 60}
    decision    {"label", "branches": [{"handle": "x", "label": "…", "guard": <JSONLogic>}],
                 "default_handle": "x"}
    approval    {"label", "gate": "none"|"ai_assign"|"role"}
    human_task  {"label", "instructions", "form": [{"key","label","type"}]}
    loop        {"label", "liste": "<kontextpfad>", "element": "element", "index": "i"}
    timer       {"label", "dauer": 30, "einheit": "m"|"h"|"t"}   oder {"bis": "<ISO>"}
    wait_event  {"label", "events": ["comment", "manual"]}
    subflow     {"label", "slot": "<slot>"}  oder {"label", "definition_id": <id>}
    agent_task  nur wenn der Ablauf an einem Ticket hängt (subject_kind=issue)
- Aktionen (`action.action`) und ihre wichtigsten Parameter:
    notify        {"to": {...}, "title", "text"} — Empfänger: {"mode":"user","user_id":N},
                  {"mode":"role","role":"owner"}, {"mode":"reporter"} oder
                  {"mode":"context","path":"<kontextpfad zur User-ID>"}. Ohne `to` geht die
                  Nachricht an den Betreiber — für einen eigenen Ablauf oft richtig.
    comment       {"text"}                        (nur bei Ticket-Abläufen)
    set_context   {"<schlüssel>": "<wert>"}       schreibt in den Kontext
    tool_call     {"tool": "<mcp-werkzeug>", "arguments": {...}, "context_key": "tool",
                   "fail_on_error": true}         → tool.ok / tool.text / tool.json
    http_request  {"destination": "<ziel>", "method", "path", "body", "query", "headers",
                   "fail_on_error": true}         → http.status_code / http.ok / http.json
    create_ticket {"project_id", "title", "description", "type", "priority"}
    set_field     {"<feld>": "<wert>"}            Felder des Artefakts
    set_status    {"status": "<agent-status>", "hold_reason": "…"}
    set_board_status {"status": "<spalte>"}
- Texte in Parametern dürfen Kontextwerte einsetzen: "{{ pfad }}", mit Filtern
  "{{ pfad | kurz:80 }}", "{{ liste | anzahl }}". Es gibt NUR die unten aufgezählten
  Filter — erfinde keine. Ohne Kontext gibt es zusätzlich "{{ jetzt }}" (Zeitpunkt) und
  "{{ heute }}" (Datum).
- Ein Ablauf, der zu einer Uhrzeit laufen soll, hat KEINEN Auslöser im Graphen: er
  bekommt keinen `trigger`, und gestartet wird er später über einen Job (Zeitplan).
  Erfinde dafür kein Ereignis.
- Bedingungen (`guard`) sind JSONLogic, z. B. {"==": [{"var": "schwere"}, "hoch"]},
  {">": [{"var": "http.status_code"}, 399]}, {"and": [ … ]}. Der Standard-Zweig braucht
  keinen guard.
- Nimm nur Werkzeuge aus der mitgelieferten Liste. Ist keins passend, lass `tool` leer
  und benenne im Label, was dort hingehört.
- Halte den Ablauf so klein wie möglich: lieber fünf Knoten, die stimmen, als zwölf,
  die etwas vortäuschen. Deutsche Labels.
"""


def _json_aus(text: str) -> dict:
    """Pull JSON out of a model answer, tolerating code fences and preambles."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                pass
    return {}


def _tiefen(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    """How far each node sits from the start, used for a readable layout."""
    start = next((n["id"] for n in nodes if n.get("type") == "start"), None)
    tiefe: dict[str, int] = {}
    if start is None:
        return tiefe
    rand, t = [start], 0
    tiefe[start] = 0
    while rand and t < 200:
        t += 1
        naechste = []
        for nid in rand:
            for e in edges:
                if e.get("source") == nid and e.get("target") not in tiefe:
                    tiefe[e["target"]] = t
                    naechste.append(e["target"])
        rand = naechste
    return tiefe


def anordnen(graph: dict) -> dict:
    """Set positions: one row per step, siblings side by side.

    The model should think about the flow, not about the layout. Without positions every
    node would sit on top of the others: the graph would be correct and still unreadable.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    tiefe = _tiefen(nodes, edges)
    belegt: dict[int, int] = {}
    for n in nodes:
        t = tiefe.get(n.get("id"), 0)
        spalte = belegt.get(t, 0)
        belegt[t] = spalte + 1
        n["position"] = {"x": spalte * SPALTE, "y": t * ZEILE}
    return {"nodes": nodes, "edges": edges}


def _config_von(n: dict) -> dict:
    """Find the config, no matter how deep the model buried it.

    The contract asks for `data.config`, but models just as happily write `config` right
    on the node or put the fields flat into `data`. Being strict here gets you a graph
    with the right shape and nothing but empty nodes, which is exactly what the first real
    run looked like: six nodes without a single label.
    """
    data = n.get("data") if isinstance(n.get("data"), dict) else {}
    for kandidat in (data.get("config"), n.get("config"), data):
        if isinstance(kandidat, dict) and kandidat:
            return {k: v for k, v in kandidat.items() if k not in ("config", "runtimeState")}
    return {}


def _nachbessern(nodes: list[dict]) -> None:
    """Close small omissions here instead of burning a model round on them.

    Both otherwise cost time and look like a system error to the person watching: a
    decision without a named default branch (validation then asks for an edge on the
    outlet `default` that nobody drew) and an end node without an outcome.
    """
    for n in nodes:
        cfg = n["data"]["config"]
        if n["type"] == "decision" and cfg.get("branches") and not cfg.get("default_handle"):
            zweige = [b for b in cfg["branches"] if isinstance(b, dict) and b.get("handle")]
            if zweige:
                ohne_guard = next((b for b in zweige if not b.get("guard")), zweige[-1])
                cfg["default_handle"] = ohne_guard["handle"]
        if n["type"] == "end" and not cfg.get("outcome"):
            cfg["outcome"] = "completed"


def _saeubern(roh: dict) -> dict:
    """Keep only what a graph may contain, and make sure edges have ids."""
    nodes, edges = [], []
    for i, n in enumerate(roh.get("nodes") or []):
        if not isinstance(n, dict) or not n.get("id") or not n.get("type"):
            continue
        nodes.append({"id": str(n["id"]), "type": str(n["type"]),
                      "data": {"config": _config_von(n)}})
    _nachbessern(nodes)
    for i, e in enumerate(roh.get("edges") or []):
        if not isinstance(e, dict) or not e.get("source") or not e.get("target"):
            continue
        kante = {"id": str(e.get("id") or f"e{i}"),
                 "source": str(e["source"]), "target": str(e["target"])}
        if e.get("sourceHandle"):
            kante["sourceHandle"] = str(e["sourceHandle"])
        if e.get("label"):
            kante["label"] = str(e["label"])[:60]
        edges.append(kante)
    return {"nodes": nodes, "edges": edges}


async def _werkzeugliste(db: AsyncSession, owner_id: int | None) -> str:
    from .workflow_tools import werkzeuge
    try:
        alle = await werkzeuge(db, owner_id)
    except Exception:  # noqa: BLE001, drawing works without the tool list too
        return ""
    zeilen = [f"- {w['name']}({', '.join(w['pflicht'][:6])}) — {w['beschreibung'][:90]}"
              for w in alle[:MAX_WERKZEUGE]]
    if not zeilen:
        return ""
    rest = max(0, len(alle) - MAX_WERKZEUGE)
    kopf = f"Verfügbare Werkzeuge ({len(alle)}"
    kopf += f", davon {rest} hier nicht aufgeführt" if rest else ""
    return kopf + "):\n" + "\n".join(zeilen)


async def entwerfen(db: AsyncSession, *, owner_id: int, beschreibung: str,
                    subject_kind: WorkflowSubjectKind, vorhanden: dict | None = None,
                    token_name: str = "") -> dict:
    """Returns {"graph": {...}, "fehler": [...], "erklaerung": "..."}.

    `vorhanden` is the graph currently on the canvas. Then this is not a new drawing but a
    rebuild ("put an approval in front of the deployment"). The difference lives in the
    prompt only, both cases return the complete graph.
    """
    token = await resolve_provider_token(db, owner_id, "claude_code", token_name)
    if not token:
        raise RuntimeError("No Claude access stored (Settings -> providers)")

    from .workflow_expr import katalog as filter_katalog

    filter_text = "Filter für {{ … | filter:arg }}:\n" + "\n".join(
        f"- {f['name']}: {f['hilfe']}" for f in filter_katalog())
    teile = [KONTRAKT, "\n" + filter_text,
             f"\nGegenstand des Ablaufs: subject_kind={subject_kind.value}."]
    if subject_kind != WorkflowSubjectKind.issue:
        teile.append("Kein Ticket im Rücken: agent_task, comment und die Ticket-Status-"
                     "Aktionen stehen NICHT zur Verfügung.")
    werkzeuge_text = await _werkzeugliste(db, owner_id)
    if werkzeuge_text:
        teile.append("\n" + werkzeuge_text)
    system = "\n".join(teile)

    if vorhanden and (vorhanden.get("nodes") or []):
        auftrag = ("Hier ist der bestehende Ablauf:\n"
                   f"{json.dumps(_saeubern(vorhanden), ensure_ascii=False)}\n\n"
                   f"Baue ihn nach diesem Wunsch um: {beschreibung}\n"
                   "Behalte, was nicht betroffen ist — inklusive der Knoten-IDs.")
    else:
        auftrag = f"Zeichne einen Ablauf für: {beschreibung}"

    verlauf = [{"role": "user", "content": auftrag}]
    graph: dict = {"nodes": [], "edges": []}
    erklaerung = ""
    fehler: list[str] = []

    for runde in range(2):
        resp = await llm_router.chat(
            provider="claude_code", model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": system}, *verlauf],
            temperature=0.2, max_tokens=8000, tokens={"claude_code": token})
        roh = _json_aus(resp.text or "")
        if not roh.get("nodes"):
            fehler = ["Das Modell hat keinen Graphen geliefert."]
            break
        erklaerung = str(roh.get("erklaerung") or "")[:500]
        graph = anordnen(_saeubern(roh))
        fehler = validate_graph(subject_kind, graph)
        if not fehler or runde == 1:
            break
        # Fix-up round with the very sentences the editor would show.
        log.info("The draft has %d errors, so one correction", len(fehler))
        verlauf += [
            {"role": "assistant", "content": resp.text or ""},
            {"role": "user", "content":
             "Die Prüfung meldet:\n" + "\n".join(f"- {f}" for f in fehler)
             + "\nGib den vollständigen, korrigierten Graphen erneut als JSON aus."},
        ]

    return {"graph": graph, "fehler": fehler, "erklaerung": erklaerung}
