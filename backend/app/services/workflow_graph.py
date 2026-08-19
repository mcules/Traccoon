"""What counts as a change to a flow, and what is merely furniture.

A version is a statement about behaviour: this flow now does something different. Dragging a
node three centimetres to the left is not that. Until now the editor could not tell the two
apart: opening a flow cloned a draft, moving a box marked it as "differs from v7", and the
version history filled up with entries in which nothing had happened (the flow
`schnee-winterreifen` collected two of them on the day it was built).

So the graph is read twice here: once as its content (`inhalts_signatur`, positions left
out), once as the difference between two contents (`unterschiede`). Everything that decides
about versions hangs off the first, everything a human wants to read hangs off the second.

The counterpart in the browser is `frontend/src/components/workflow/convert.ts`; both have to
answer the same way, otherwise the editor shows a change the server does not see.
"""
from __future__ import annotations

import json


def _sortiert(wert):
    """Deep sort of dict keys so that a re-saved graph does not look different.

    Lists keep their order: in a graph the order of nodes says nothing, but the order inside
    a config (the branches of a decision) says everything.
    """
    if isinstance(wert, dict):
        return {k: _sortiert(wert[k]) for k in sorted(wert)
                if wert[k] is not None}
    if isinstance(wert, list):
        return [_sortiert(v) for v in wert]
    return wert


def knoten_inhalt(node: dict) -> dict:
    """One node, without anything that only concerns the picture."""
    return {"id": str(node.get("id") or ""), "type": str(node.get("type") or ""),
            "config": _sortiert((node.get("data") or {}).get("config") or {})}


def kanten_inhalt(edge: dict) -> dict:
    """One edge. The id travels along because the editor addresses edges by it, but the
    meaning sits in source, handle and target."""
    return {"id": str(edge.get("id") or ""), "source": str(edge.get("source") or ""),
            "handle": str(edge.get("sourceHandle") or ""),
            "target": str(edge.get("target") or ""),
            "label": edge.get("label") if isinstance(edge.get("label"), str) else ""}


def inhalt(graph: dict | None) -> dict:
    """The functional content of a graph: nodes and edges, sorted, without positions."""
    graph = graph or {}
    return {
        "nodes": sorted((knoten_inhalt(n) for n in (graph.get("nodes") or [])),
                        key=lambda n: n["id"]),
        "edges": sorted((kanten_inhalt(e) for e in (graph.get("edges") or [])),
                        key=lambda e: (e["source"], e["handle"], e["target"], e["id"])),
    }


def inhalts_signatur(graph: dict | None) -> str:
    """One string per behaviour. Equal string, equal flow."""
    return json.dumps(inhalt(graph), sort_keys=True, ensure_ascii=False)


def gleicher_inhalt(a: dict | None, b: dict | None) -> bool:
    return inhalts_signatur(a) == inhalts_signatur(b)


def positionen(graph: dict | None) -> dict[str, dict]:
    """Node id to position, the half a version does not care about."""
    out: dict[str, dict] = {}
    for node in (graph or {}).get("nodes") or []:
        pos = node.get("position") or {}
        out[str(node.get("id") or "")] = {"x": pos.get("x", 0), "y": pos.get("y", 0)}
    return out


def mit_positionen(graph: dict | None, neue: dict[str, dict]) -> dict:
    """A copy of the graph with the given positions, content untouched.

    Used to save an arrangement into a version that has already been published: the flow does
    not change by it, so the version does not have to.
    """
    kopie = json.loads(json.dumps(graph or {"nodes": [], "edges": []}))
    for node in kopie.get("nodes") or []:
        pos = neue.get(str(node.get("id") or ""))
        if pos:
            node["position"] = {"x": pos.get("x", 0), "y": pos.get("y", 0)}
    return kopie


def _flach(wert, praefix: str = "") -> dict[str, object]:
    """A config as flat paths: `action.params.reihe` instead of one lump called `action`.

    Whoever compares whole configs learns that "the action changed" and has to read two
    pages of JSON to find out what. Lists stay whole: their order carries meaning (the
    branches of a decision), and an index in the path would be more confusing than helpful.
    """
    if isinstance(wert, dict):
        out: dict[str, object] = {}
        for k in sorted(wert):
            out.update(_flach(wert[k], f"{praefix}.{k}" if praefix else str(k)))
        return out
    return {praefix: wert}


def _label(node: dict) -> str:
    cfg = node.get("config") or {}
    return str(cfg.get("label") or node.get("id") or "")


def _kante_text(kante: dict) -> str:
    pfeil = f"{kante['source']} → {kante['target']}"
    return f"{pfeil} ({kante['handle']})" if kante["handle"] else pfeil


def unterschiede(alt: dict | None, neu: dict | None) -> dict:
    """What changed between two graphs, in the words of the editor.

    Deliberately not a text diff over JSON: a moved brace is not an answer to "what does the
    flow do differently now". Nodes are compared by id, edges by where they run, and a
    changed node says WHICH of its settings changed, because that is the line a human looks
    for.
    """
    a, b = inhalt(alt), inhalt(neu)
    a_knoten = {n["id"]: n for n in a["nodes"]}
    b_knoten = {n["id"]: n for n in b["nodes"]}
    a_kanten = {(e["source"], e["handle"], e["target"]): e for e in a["edges"]}
    b_kanten = {(e["source"], e["handle"], e["target"]): e for e in b["edges"]}

    geaendert = []
    for nid in sorted(set(a_knoten) & set(b_knoten)):
        vorher, nachher = a_knoten[nid]["config"], b_knoten[nid]["config"]
        if vorher == nachher and a_knoten[nid]["type"] == b_knoten[nid]["type"]:
            continue
        v_flach, n_flach = _flach(vorher), _flach(nachher)
        felder = sorted(set(v_flach) | set(n_flach))
        geaendert.append({
            "id": nid, "label": _label(b_knoten[nid]),
            "felder": [{"feld": f,
                        "vorher": json.dumps(v_flach.get(f), ensure_ascii=False)[:400],
                        "nachher": json.dumps(n_flach.get(f), ensure_ascii=False)[:400]}
                       for f in felder if v_flach.get(f) != n_flach.get(f)],
        })

    return {
        "knoten_neu": [{"id": n, "label": _label(b_knoten[n])}
                       for n in sorted(set(b_knoten) - set(a_knoten))],
        "knoten_weg": [{"id": n, "label": _label(a_knoten[n])}
                       for n in sorted(set(a_knoten) - set(b_knoten))],
        "knoten_geaendert": geaendert,
        "kanten_neu": [_kante_text(b_kanten[k]) for k in sorted(set(b_kanten) - set(a_kanten))],
        "kanten_weg": [_kante_text(a_kanten[k]) for k in sorted(set(a_kanten) - set(b_kanten))],
        "gleich": inhalts_signatur(alt) == inhalts_signatur(neu),
    }
