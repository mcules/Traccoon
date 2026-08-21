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


def _sorted(value):
    """Deep sort of dict keys so that a re-saved graph does not look different.

    Lists keep their order: in a graph the order of nodes says nothing, but the order inside
    a config (the branches of a decision) says everything.
    """
    if isinstance(value, dict):
        return {k: _sorted(value[k]) for k in sorted(value)
                if value[k] is not None}
    if isinstance(value, list):
        return [_sorted(v) for v in value]
    return value


def node_content(node: dict) -> dict:
    """One node, without anything that only concerns the picture."""
    return {"id": str(node.get("id") or ""), "type": str(node.get("type") or ""),
            "config": _sorted((node.get("data") or {}).get("config") or {})}


def edges_content(edge: dict) -> dict:
    """One edge. The id travels along because the editor addresses edges by it, but the
    meaning sits in source, handle and target."""
    return {"id": str(edge.get("id") or ""), "source": str(edge.get("source") or ""),
            "handle": str(edge.get("sourceHandle") or ""),
            "target": str(edge.get("target") or ""),
            "label": edge.get("label") if isinstance(edge.get("label"), str) else ""}


def content(graph: dict | None) -> dict:
    """The functional content of a graph: nodes and edges, sorted, without positions."""
    graph = graph or {}
    return {
        "nodes": sorted((node_content(n) for n in (graph.get("nodes") or [])),
                        key=lambda n: n["id"]),
        "edges": sorted((edges_content(e) for e in (graph.get("edges") or [])),
                        key=lambda e: (e["source"], e["handle"], e["target"], e["id"])),
    }


def content_signature(graph: dict | None) -> str:
    """One string per behaviour. Equal string, equal flow."""
    return json.dumps(content(graph), sort_keys=True, ensure_ascii=False)


def same_content(a: dict | None, b: dict | None) -> bool:
    return content_signature(a) == content_signature(b)


def positions(graph: dict | None) -> dict[str, dict]:
    """Node id to position, the half a version does not care about."""
    out: dict[str, dict] = {}
    for node in (graph or {}).get("nodes") or []:
        pos = node.get("position") or {}
        out[str(node.get("id") or "")] = {"x": pos.get("x", 0), "y": pos.get("y", 0)}
    return out


def with_positions(graph: dict | None, new: dict[str, dict]) -> dict:
    """A copy of the graph with the given positions, content untouched.

    Used to save an arrangement into a version that has already been published: the flow does
    not change by it, so the version does not have to.
    """
    copy = json.loads(json.dumps(graph or {"nodes": [], "edges": []}))
    for node in copy.get("nodes") or []:
        pos = new.get(str(node.get("id") or ""))
        if pos:
            node["position"] = {"x": pos.get("x", 0), "y": pos.get("y", 0)}
    return copy


def _flat(value, prefix: str = "") -> dict[str, object]:
    """A config as flat paths: `action.params.reihe` instead of one lump called `action`.

    Whoever compares whole configs learns that "the action changed" and has to read two
    pages of JSON to find out what. Lists stay whole: their order carries meaning (the
    branches of a decision), and an index in the path would be more confusing than helpful.
    """
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k in sorted(value):
            out.update(_flat(value[k], f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: value}


def _label(node: dict) -> str:
    cfg = node.get("config") or {}
    return str(cfg.get("label") or node.get("id") or "")


def _edge_text(edge: dict) -> str:
    arrow = f"{edge['source']} → {edge['target']}"
    return f"{arrow} ({edge['handle']})" if edge["handle"] else arrow


def differences(old: dict | None, new: dict | None) -> dict:
    """What changed between two graphs, in the words of the editor.

    Deliberately not a text diff over JSON: a moved brace is not an answer to "what does the
    flow do differently now". Nodes are compared by id, edges by where they run, and a
    changed node says WHICH of its settings changed, because that is the line a human looks
    for.
    """
    a, b = content(old), content(new)
    a_node = {n["id"]: n for n in a["nodes"]}
    b_node = {n["id"]: n for n in b["nodes"]}
    a_edges = {(e["source"], e["handle"], e["target"]): e for e in a["edges"]}
    b_edges = {(e["source"], e["handle"], e["target"]): e for e in b["edges"]}

    changed = []
    for nid in sorted(set(a_node) & set(b_node)):
        before, nachher = a_node[nid]["config"], b_node[nid]["config"]
        if before == nachher and a_node[nid]["type"] == b_node[nid]["type"]:
            continue
        v_flat, n_flat = _flat(before), _flat(nachher)
        fields = sorted(set(v_flat) | set(n_flat))
        changed.append({
            "id": nid, "label": _label(b_node[nid]),
            "fields": [{"field": f,
                        "before": json.dumps(v_flat.get(f), ensure_ascii=False)[:400],
                        "after": json.dumps(n_flat.get(f), ensure_ascii=False)[:400]}
                       for f in fields if v_flat.get(f) != n_flat.get(f)],
        })

    return {
        "nodes_added": [{"id": n, "label": _label(b_node[n])}
                       for n in sorted(set(b_node) - set(a_node))],
        "nodes_removed": [{"id": n, "label": _label(a_node[n])}
                       for n in sorted(set(a_node) - set(b_node))],
        "nodes_changed": changed,
        "edges_added": [_edge_text(b_edges[k]) for k in sorted(set(b_edges) - set(a_edges))],
        "edges_removed": [_edge_text(a_edges[k]) for k in sorted(set(a_edges) - set(b_edges))],
        "identical": content_signature(old) == content_signature(new),
    }
