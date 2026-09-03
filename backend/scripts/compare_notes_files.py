#!/usr/bin/env python3
"""Ask both sides the same thing and say where they disagree.

The port is not finished when it runs, it is finished when it answers what the
service before it answered. So: the tree, and then every note in it, from both,
compared.

Only reading is compared, which is all the new side can do today. Writing is not
switched on until it has been through review; an untested writer on somebody's
notes is not a thing to turn on quietly.

    python scripts/compare_notes_files.py            tree and 200 notes
    python scripts/compare_notes_files.py --all      tree and every note
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

OLD = os.environ.get("NOTES_BASE_URL", "").rstrip("/")
NEW = os.environ.get("NOTES_NATIVE_URL", "http://127.0.0.1:8800").rstrip("/")
TOKEN = os.environ.get("NOTES_TOKEN", "")
OLD_HEADERS = {"Remote-User": os.environ.get("NOTES_USER", "compare")}
NEW_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def flatten(node: dict, out: dict[str, dict]) -> dict[str, dict]:
    """The tree as a flat map, so a difference names a path and not a position."""
    for child in node.get("children", []):
        out[child["path"]] = {k: v for k, v in child.items() if k != "children"}
        if child.get("type") == "folder":
            flatten(child, out)
    return out


def compare_tree(old: httpx.Client, new: httpx.Client) -> list[str]:
    a = flatten(old.get(f"{OLD}/api/files/").json(), {})
    b = flatten(new.get(f"{NEW}/api/notes-native/files/").json(), {})
    fehler = []
    nur_alt = sorted(set(a) - set(b))
    nur_neu = sorted(set(b) - set(a))
    for p in nur_alt[:10]:
        fehler.append(f"only in the old tree: {p}")
    for p in nur_neu[:10]:
        fehler.append(f"only in the new tree: {p}")
    if len(nur_alt) > 10 or len(nur_neu) > 10:
        fehler.append(f"... {len(nur_alt)} only old, {len(nur_neu)} only new")
    # Compared are the fields the old side actually sends. It leaves `size` out
    # of the tree, and the new one carries it; more information is not a
    # difference, and demanding the same gaps would freeze a shape that is only
    # kept for as long as the two run side by side.
    #
    # Times are left out entirely: `ctime` means one thing on one file system and
    # something else on another, and says nothing about whether the tree is right.
    for p in sorted(set(a) & set(b)):
        for feld in ("type", "ext", "size"):
            alt_wert = a[p].get(feld)
            if alt_wert is None:
                continue
            if alt_wert != b[p].get(feld):
                fehler.append(f"{p}: {feld} {alt_wert!r} vs {b[p].get(feld)!r}")
                break
    return fehler


def compare_notes(old: httpx.Client, new: httpx.Client, limit: int | None) -> tuple[int, list[str]]:
    tree = flatten(old.get(f"{OLD}/api/files/").json(), {})
    notes = [p for p, n in sorted(tree.items())
             if n.get("type") == "file" and p.lower().endswith((".md", ".markdown"))]
    if limit:
        # Spread over the vault rather than the first N, which would all sit in
        # the same folder and say nothing about the rest.
        step = max(1, len(notes) // limit)
        notes = notes[::step][:limit]
    fehler = []
    for p in notes:
        ra = old.get(f"{OLD}/api/files/content", params={"path": p})
        rb = new.get(f"{NEW}/api/notes-native/files/content", params={"path": p})
        if ra.status_code != rb.status_code:
            fehler.append(f"{p}: status {ra.status_code} vs {rb.status_code}")
            continue
        if ra.status_code != 200:
            continue
        a, b = ra.json(), rb.json()
        if a["content"] != b["content"]:
            fehler.append(f"{p}: the text differs ({len(a['content'])} vs {len(b['content'])})")
        elif a["hash"] != b["hash"]:
            fehler.append(f"{p}: same text, different hash {a['hash']} vs {b['hash']}")
    return len(notes), fehler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every note, not a sample")
    args = ap.parse_args()
    if not OLD or not TOKEN:
        print("NOTES_BASE_URL and NOTES_TOKEN have to be set", file=sys.stderr)
        return 2

    with httpx.Client(headers=OLD_HEADERS, timeout=60) as old, \
         httpx.Client(headers=NEW_HEADERS, timeout=60) as new:
        baum = compare_tree(old, new)
        print(f"tree: {'same' if not baum else str(len(baum)) + ' differences'}")
        for f in baum[:20]:
            print(f"  {f}")
        anzahl, texte = compare_notes(old, new, None if args.all else 200)
        print(f"notes: {anzahl} compared, "
              f"{'all the same' if not texte else str(len(texte)) + ' different'}")
        for f in texte[:20]:
            print(f"  {f}")
    return 1 if (baum or texte) else 0


if __name__ == "__main__":
    sys.exit(main())
