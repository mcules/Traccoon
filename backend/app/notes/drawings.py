"""Getting the drawing out of a drawing file, and putting it back.

Two shapes live in the vault. A plain `.excalidraw` file is the scene as JSON
and nothing else. An `.excalidraw.md` file is a note: a warning line for anyone
opening it as text, the drawing's own text elements as readable markdown so a
search finds them, and the scene itself in a fenced block at the end — as JSON,
or compressed, which is what recent versions write.

Only the scene is of interest. The markdown around it is written by one program
for itself, and none of it is needed to draw the picture — but all of it is kept
on the way back, because it also carries what somebody wrote about the drawing.
"""
from __future__ import annotations

import json
import re
from typing import Any

import lzstring

# The fenced block holding the scene, in whichever of the two forms.
BLOCK = re.compile(r"```(compressed-json|json)\r?\n([\s\S]*?)\r?\n```")
LINE_WIDTH = 200

_lz = lzstring.LZString()

EMPTY: dict[str, Any] = {
    "type": "excalidraw",
    "version": 2,
    "elements": [],
    "appState": {"viewBackgroundColor": "#ffffff"},
    "files": {},
}


def _normalise(raw: Any) -> dict:
    o = raw if isinstance(raw, dict) else {}
    state = o.get("appState") if isinstance(o.get("appState"), dict) else {}
    out = {
        "type": o["type"] if isinstance(o.get("type"), str) else "excalidraw",
        "version": o["version"] if isinstance(o.get("version"), (int, float)) else 2,
        # `collaborators` is a map while a drawing is open and a leftover in the
        # file; handing it back makes the editor complain about a shape it
        # cannot use.
        "appState": {k: v for k, v in state.items() if k != "collaborators"},
        "elements": o["elements"] if isinstance(o.get("elements"), list) else [],
        "files": o["files"] if isinstance(o.get("files"), dict) else {},
    }
    if isinstance(o.get("source"), str):
        out["source"] = o["source"]
    return out


def read(source: str) -> dict:
    """The scene in a file, whichever shape the file has.

    An unreadable one gives an empty drawing rather than an error: what is on
    the screen then is an empty canvas somebody can draw on, and what is on disk
    is untouched until they save.
    """
    text = source.strip()
    if text.startswith("{"):
        try:
            return _normalise(json.loads(text))
        except ValueError:
            return dict(EMPTY)

    found = BLOCK.search(source)
    if not found:
        return dict(EMPTY)
    body = found.group(2)
    if found.group(1) == "compressed-json":
        # Broken across lines to keep the file readable-ish; the breaks are not
        # part of the data.
        try:
            body = _lz.decompressFromBase64(body.replace("\r", "").replace("\n", "")) or ""
        except Exception:            # noqa: BLE001 - see below
            # The library raises on a character it does not expect rather than
            # saying it cannot read this. A half-written or hand-edited block is
            # a drawing that cannot be read, not a fault of the server.
            body = ""
        if not body:
            return dict(EMPTY)
    try:
        return _normalise(json.loads(body))
    except ValueError:
        return dict(EMPTY)


def _chunk(text: str, width: int = LINE_WIDTH) -> str:
    return "\n".join(text[i:i + width] for i in range(0, len(text), width))


def write(original: str, scene: dict, *, source_name: str = "notes") -> str:
    """Put a scene back, keeping everything around it.

    The wrapper carries more than the drawing — the text section, the properties
    at the top, what a note says about the picture. Only the fenced block is
    replaced, and in the form the file already used: one that was compressed
    stays compressed, so opening it elsewhere afterwards shows no difference
    other than the drawing itself.
    """
    full = dict(scene)
    full["type"] = "excalidraw"
    full["version"] = scene.get("version") or 2
    full["source"] = scene.get("source") or source_name
    # Separators without spaces, the way the format is written everywhere else.
    packed = json.dumps(full, ensure_ascii=False, separators=(",", ":"))

    if original.strip().startswith("{"):
        # A plain file is the scene and nothing else, and it is also read by
        # people — so it keeps its indentation.
        return json.dumps(full, ensure_ascii=False, indent=2) + "\n"

    found = BLOCK.search(original)
    if not found:
        # A note with no block yet: append one rather than lose what is there.
        return (f"{original.rstrip()}\n\n## Drawing\n"
                f"```compressed-json\n{_chunk(_lz.compressToBase64(packed))}\n```\n%%\n")
    kind = found.group(1)
    body = (_chunk(_lz.compressToBase64(packed)) if kind == "compressed-json"
            else json.dumps(full, ensure_ascii=False, indent=2))
    return original[:found.start()] + f"```{kind}\n{body}\n```" + original[found.end():]


def is_drawing(rel: str) -> bool:
    return bool(re.search(r"\.excalidraw(\.md)?$", rel, re.I))
