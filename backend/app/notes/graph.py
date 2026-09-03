"""The vault as a picture: which note points at which.

Three kinds of dot. A note that exists. An attachment, which is a file that is
embedded but is not a note. And a link that points at nothing — which is not an
error to hide but the most interesting kind of dot there is: it says a note was
meant and never written.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .index.links import LinkGraph
from .model.note import link_key, strip_note_suffix

# A target ending in one of these is an attachment, not a note somebody forgot
# to write. The list is the one the format itself embeds.
ATTACHMENT = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|bmp|ico|avif|pdf|mp4|webm|mov|mkv|mp3|wav|ogg"
    r"|m4a|flac|zip|docx?|xlsx?|pptx?)$", re.I)


def build(graph: LinkGraph) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_node: set[str] = set()
    seen_edge: set[str] = set()

    def add_node(node: dict) -> None:
        if node["id"] in seen_node:
            return
        seen_node.add(node["id"])
        nodes.append(node)

    def add_edge(source: str, target: str) -> None:
        # A note that links to itself is a dot with a loop and says nothing.
        if source == target:
            return
        key = f"{source} {target}"
        if key in seen_edge:
            return
        seen_edge.add(key)
        edges.append({"source": source, "target": target})

    for rel in graph.outgoing:
        add_node({"id": rel,
                  "label": strip_note_suffix(PurePosixPath(rel).name),
                  "kind": "note",
                  "tags": graph.tags.get(rel, [])})

    for source, targets in graph.raw_links.items():
        for target in targets:
            found = graph.key_to_path.get(link_key(target))
            if found:
                add_edge(source, found)
            elif ATTACHMENT.search(target):
                node_id = f"attachment:{target.lower()}"
                add_node({"id": node_id, "label": PurePosixPath(target).name,
                          "kind": "attachment", "tags": []})
                add_edge(source, node_id)
            else:
                node_id = f"unresolved:{link_key(target)}"
                add_node({"id": node_id, "label": target,
                          "kind": "unresolved", "tags": []})
                add_edge(source, node_id)

    return {"nodes": nodes, "edges": edges}
