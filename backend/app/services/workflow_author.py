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
MAX_TOOLS = 220          # prompt cap, the picker in the editor stays complete
COLUMN, LINE = 260, 130     # same grid the shipped graphs use

CONTRACT = """\
You draw flows for Traccoon as a directed graph. Answer ONLY with a JSON object, without prose
and without code fences:

{"explanation": "1-3 sentences on what the flow does", "nodes": [...], "edges": [...]}

A node: {"id": "short_and_unique", "type": "<type>", "data": {"config": {...}}}
An edge: {"id": "e1", "source": "<node>", "target": "<node>", "sourceHandle": "<outlet or none>", "label": "optional"}
Leave the positions out — they are set for you.

RULES (they are checked; breaking one makes the flow unusable):
- Exactly ONE start node, at least one end node, every end reachable from the start.
- Every node except start has an incoming edge, every one except end an outgoing one.
- Nodes with fixed outlets need an edge for EVERY one of them:
    decision  → one `handle` per branch, plus the default branch (`default_handle`,
                which MUST be one of the branches)
    approval  → "approved" and "rejected"
    loop      → "element" (the loop body) and "fertig"; the body leads back to the loop
                with an edge WITHOUT a sourceHandle
    subflow   → "completed" and "failed"
    auto_action → the normal one without a sourceHandle; additionally an optional "error"
- Node types and their configuration:
    start       {"label", "trigger": {"kind": "webhook"|"event", "event": "...",
                 "sample": {...example payload...}}}  — without a trigger: a manual start or a job
    end         {"label", "outcome": "completed"|"failed"}
    auto_action {"label", "action": {"action": "<name>", "params": {...}},
                 "retries": 3, "retry_wait_sec": 60}
    decision    {"label", "branches": [{"handle": "x", "label": "…", "guard": <JSONLogic>}],
                 "default_handle": "x"}
    approval    {"label", "gate": "none"|"ai_assign"|"role"}
    human_task  {"label", "instructions", "form": [{"key","label","type"}]}
    loop        {"label", "list": "<context path>", "element": "element", "index": "i"}
    timer       {"label", "duration": 30, "unit": "m"|"h"|"d"}   or {"until": "<ISO>"}
    wait_event  {"label", "events": ["comment", "manual"]}
    subflow     {"label", "slot": "<slot>"}  or {"label", "definition_id": <id>}
    agent_task  only when the flow hangs on a ticket (subject_kind=issue)
- Actions (`action.action`) and their most important parameters:
    notify        {"to": {...}, "title", "text"} — recipient: {"mode":"user","user_id":N},
                  {"mode":"role","role":"owner"}, {"mode":"reporter"} or
                  {"mode":"context","path":"<context path to the user id>"}. Without `to` the
                  message goes to the owner — often the right thing for a flow of your own.
    comment       {"text"}                        (only in ticket flows)
    set_context   {"<key>": "<value>"}            writes into the context
    tool_call     {"tool": "<mcp tool>", "arguments": {...}, "context_key": "tool",
                   "fail_on_error": true}         → tool.ok / tool.text / tool.json
    http_request  {"destination": "<destination>", "method", "path", "body", "query", "headers",
                   "fail_on_error": true}         → http.status_code / http.ok / http.json
    create_ticket {"project_id", "title", "description", "type", "priority"}
    set_field     {"<field>": "<value>"}          fields of the artifact
    set_status    {"status": "<agent status>", "hold_reason": "…"}
    set_board_status {"status": "<column>"}
- Texts in parameters may insert context values: "{{ path }}", with filters
  "{{ path | truncate:80 }}", "{{ list | count }}". ONLY the filters listed below exist —
  invent none. Without a context there are additionally "{{ now }}" (a point in time) and
  "{{ today }}" (a date).
- A flow that is meant to run at a certain time has NO trigger in the graph: it gets no
  `trigger`, and it is started later through a job (a schedule). Do not invent an event for
  that.
- Conditions (`guard`) are JSONLogic, {"==": [{"var": "severity"}, "high"]} for instance,
  {">": [{"var": "http.status_code"}, 399]}, {"and": [ … ]}. The default branch needs no guard.
- Take tools from the supplied list only. If none fits, leave `tool` empty and name in the
  label what belongs there.
- Keep the flow as small as possible: five nodes that are right beat twelve that pretend
  something. Write the labels in English.
"""


def _json_from(text: str) -> dict:
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


def _deep(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    """How far each node sits from the start, used for a readable layout."""
    start = next((n["id"] for n in nodes if n.get("type") == "start"), None)
    depth: dict[str, int] = {}
    if start is None:
        return depth
    edge, t = [start], 0
    depth[start] = 0
    while edge and t < 200:
        t += 1
        next_ones = []
        for nid in edge:
            for e in edges:
                if e.get("source") == nid and e.get("target") not in depth:
                    depth[e["target"]] = t
                    next_ones.append(e["target"])
        edge = next_ones
    return depth


def arrange(graph: dict) -> dict:
    """Set positions: one row per step, siblings side by side.

    The model should think about the flow, not about the layout. Without positions every
    node would sit on top of the others: the graph would be correct and still unreadable.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    depth = _deep(nodes, edges)
    taken: dict[int, int] = {}
    for n in nodes:
        t = depth.get(n.get("id"), 0)
        column = taken.get(t, 0)
        taken[t] = column + 1
        n["position"] = {"x": column * COLUMN, "y": t * LINE}
    return {"nodes": nodes, "edges": edges}


def _config_from(n: dict) -> dict:
    """Find the config, no matter how deep the model buried it.

    The contract asks for `data.config`, but models just as happily write `config` right
    on the node or put the fields flat into `data`. Being strict here gets you a graph
    with the right shape and nothing but empty nodes, which is exactly what the first real
    run looked like: six nodes without a single label.
    """
    data = n.get("data") if isinstance(n.get("data"), dict) else {}
    for candidate in (data.get("config"), n.get("config"), data):
        if isinstance(candidate, dict) and candidate:
            return {k: v for k, v in candidate.items() if k not in ("config", "runtimeState")}
    return {}


def _repair(nodes: list[dict]) -> None:
    """Close small omissions here instead of burning a model round on them.

    Both otherwise cost time and look like a system error to the person watching: a
    decision without a named default branch (validation then asks for an edge on the
    outlet `default` that nobody drew) and an end node without an outcome.
    """
    for n in nodes:
        cfg = n["data"]["config"]
        if n["type"] == "decision" and cfg.get("branches") and not cfg.get("default_handle"):
            branches = [b for b in cfg["branches"] if isinstance(b, dict) and b.get("handle")]
            if branches:
                without_guard = next((b for b in branches if not b.get("guard")), branches[-1])
                cfg["default_handle"] = without_guard["handle"]
        if n["type"] == "end" and not cfg.get("outcome"):
            cfg["outcome"] = "completed"


def _clean(raw: dict) -> dict:
    """Keep only what a graph may contain, and make sure edges have ids."""
    nodes, edges = [], []
    for i, n in enumerate(raw.get("nodes") or []):
        if not isinstance(n, dict) or not n.get("id") or not n.get("type"):
            continue
        nodes.append({"id": str(n["id"]), "type": str(n["type"]),
                      "data": {"config": _config_from(n)}})
    _repair(nodes)
    for i, e in enumerate(raw.get("edges") or []):
        if not isinstance(e, dict) or not e.get("source") or not e.get("target"):
            continue
        edge = {"id": str(e.get("id") or f"e{i}"),
                 "source": str(e["source"]), "target": str(e["target"])}
        if e.get("sourceHandle"):
            edge["sourceHandle"] = str(e["sourceHandle"])
        if e.get("label"):
            edge["label"] = str(e["label"])[:60]
        edges.append(edge)
    return {"nodes": nodes, "edges": edges}


async def _toollist(db: AsyncSession, owner_id: int | None) -> str:
    from .workflow_tools import tools
    try:
        all_rows = await tools(db, owner_id)
    except Exception:  # noqa: BLE001, drawing works without the tool list too
        return ""
    lines = [f"- {w['name']}({', '.join(w['pflicht'][:6])}) — {w['beschreibung'][:90]}"
              for w in all_rows[:MAX_TOOLS]]
    if not lines:
        return ""
    remainder = max(0, len(all_rows) - MAX_TOOLS)
    header = f"Available tools ({len(all_rows)}"
    header += f", {remainder} of them not listed here" if remainder else ""
    return header + "):\n" + "\n".join(lines)


async def compose(db: AsyncSession, *, owner_id: int, description: str,
                    subject_kind: WorkflowSubjectKind, existing: dict | None = None,
                    token_name: str = "") -> dict:
    """Returns {"graph": {...}, "errors": [...], "explanation": "..."}.

    `vorhanden` is the graph currently on the canvas. Then this is not a new drawing but a
    rebuild ("put an approval in front of the deployment"). The difference lives in the
    prompt only, both cases return the complete graph.
    """
    token = await resolve_provider_token(db, owner_id, "claude_code", token_name)
    if not token:
        raise RuntimeError("No Claude access stored (Settings -> providers)")

    from .workflow_expr import catalog as filter_catalog

    filter_text = "Filters for {{ … | filter:arg }}:\n" + "\n".join(
        f"- {f['name']}: {f['hilfe']}" for f in filter_catalog())
    parts = [CONTRACT, "\n" + filter_text,
             f"\nGegenstand des Ablaufs: subject_kind={subject_kind.value}."]
    if subject_kind != WorkflowSubjectKind.issue:
        parts.append("No ticket behind it: agent_task, comment and the ticket status actions "
                     "are NOT available.")
    tools_text = await _toollist(db, owner_id)
    if tools_text:
        parts.append("\n" + tools_text)
    system = "\n".join(parts)

    if existing and (existing.get("nodes") or []):
        task = ("Here is the existing flow:\n"
                   f"{json.dumps(_clean(existing), ensure_ascii=False)}\n\n"
                   f"Rebuild it along this wish: {description}\n"
                   "Keep what is not affected — the node ids included.")
    else:
        task = f"Draw a flow for: {description}"

    history = [{"role": "user", "content": task}]
    graph: dict = {"nodes": [], "edges": []}
    explanation = ""
    error: list[str] = []

    for round_no in range(2):
        resp = await llm_router.chat(
            provider="claude_code", model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": system}, *history],
            temperature=0.2, max_tokens=8000, tokens={"claude_code": token})
        raw = _json_from(resp.text or "")
        if not raw.get("nodes"):
            error = ["Das Modell hat keinen Graphen geliefert."]
            break
        explanation = str(raw.get("explanation") or raw.get("erklaerung") or "")[:500]
        graph = arrange(_clean(raw))
        error = validate_graph(subject_kind, graph)
        if not error or round_no == 1:
            break
        # Fix-up round with the very sentences the editor would show.
        log.info("The draft has %d errors, so one correction", len(error))
        history += [
            {"role": "assistant", "content": resp.text or ""},
            {"role": "user", "content":
             "The check reports:\n" + "\n".join(f"- {f}" for f in error)
             + "\nOutput the complete, corrected graph as JSON again."},
        ]

    return {"graph": graph, "errors": error, "explanation": explanation}
