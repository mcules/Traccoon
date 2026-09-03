#!/usr/bin/env python3
"""Measure the ported query language against what the old service answered.

The reference cases were recorded from the running service over the real vault
(`record_notes_reference.py`). This runs the same cases through the Python side
and says, per case, whether the answer is the same.

**The recording ages.** It is a snapshot of a living vault, and the vault keeps
being written in — a task ticked off, a note added. A grouped task query then
answers differently for a good reason. Before reading a difference as a fault,
check the same case against the previous version of the code: if it differs
there too, the vault moved and not the port.

Order is reported apart from content, on purpose. A query with no `SORT` has no
defined order on the old side: its rows come out in the order the file system
handed the notes over, which changes when a neighbouring file is created. Here
the vault is walked sorted, so the answer is the same twice. Counting that as a
difference would drown the differences that matter, and counting it as a match
would hide a real reordering — so it is its own column.

    python scripts/compare_notes_queries.py [--kind dataview] [--show 5]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.notes.dv import index as dv_index                    # noqa: E402
from app.notes.dv import settings as plugin_settings          # noqa: E402
from app.notes.dv import tasks as dv_tasks                     # noqa: E402
from app.notes.dv.bases import run as dv_bases                 # noqa: E402
from app.notes.dv.dql import execute                          # noqa: E402
from app.notes.dv.js import jsonable                          # noqa: E402
from app.notes.vault.files import Vault                       # noqa: E402

VAULT = Path(os.environ.get("VAULT_PATH", "/vault"))
REFERENCE = Path(os.environ.get("NOTES_REFERENCE_DIR", "/data/notes-reference"))
# Where the settings of the two plugins sit inside the vault. No default: the
# folder belongs to a program that is being switched off and its name is not
# something this repository carries.
PLUGIN_DIRS = {
    "query": os.environ.get("NOTES_QUERY_SETTINGS_DIR", ""),
    "tasks": os.environ.get("NOTES_TASK_SETTINGS_DIR", ""),
}


def wire(value):
    """A value as it would look on the wire, so two answers can be compared."""
    return json.loads(json.dumps(jsonable(value), ensure_ascii=False, default=str))


def without_additions(got, want):
    """`got` with everything the old side did not answer taken out.

    The port may carry more than the old service did — a group heading now
    travels as a key beside the finished sentence, so the interface can say it
    in its own language. What it must not do is change or drop what was there.
    Dropping the additions before comparing is what tells those two apart.
    """
    if isinstance(want, dict) and isinstance(got, dict):
        return {k: without_additions(got[k], want[k]) for k in want if k in got}
    if isinstance(want, list) and isinstance(got, list):
        return [without_additions(g, w) for g, w in zip(got, want)]
    return got


def sorted_deep(value):
    """The same answer with every list in a fixed order, to ask about content
    alone. Lists are compared by their text, which is enough to tell a
    reordering from a different set."""
    if isinstance(value, list):
        return sorted((sorted_deep(v) for v in value),
                      key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
    if isinstance(value, dict):
        return {k: sorted_deep(v) for k, v in value.items()}
    return value


def answer(index, case: dict):
    """Run one case the way the route that recorded it would have run it."""
    kind = case["kind"]
    if kind == "dataview":
        return execute(index, case["query"], case.get("path") or None)
    if kind == "tasks":
        return dv_tasks.execute(index, case["query"])
    if kind == "base":
        path = case["path"]
        return dv_bases.run(index, (VAULT / path).read_text(encoding="utf-8"))
    raise SystemExit(f"nothing here answers a {kind!r} case yet")


def load_cases(kind: str) -> list[dict]:
    out = []
    for path in sorted(REFERENCE.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        case = entry.get("case", {})
        answer = entry.get("answer", {})
        if case.get("kind") != kind or "body" not in answer:
            continue
        out.append({"name": path.stem, **case, "expected": answer["body"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="dataview")
    ap.add_argument("--show", type=int, default=5, help="how many differences to print")
    args = ap.parse_args()

    if not REFERENCE.is_dir():
        print(f"no recording at {REFERENCE} — nothing to measure against")
        return 2

    plugin_settings.load(VAULT, {k: v for k, v in PLUGIN_DIRS.items() if v})
    index = dv_index.build(Vault(VAULT))

    cases = load_cases(args.kind)
    print(f"{len(cases)} {args.kind} cases, {len(index.pages)} notes\n")

    tally = Counter()
    shown = 0
    for case in cases:
        try:
            got = wire(answer(index, case))
        except Exception as err:                             # noqa: BLE001
            tally["raised"] += 1
            if shown < args.show:
                shown += 1
                print(f"--- raised: {err}\n{case['query'][:400]}\n")
            continue
        want = case["expected"]
        if got == want:
            tally["identical"] += 1
            continue
        trimmed = without_additions(got, want)
        if trimmed == want:
            tally["identical, with additions"] += 1
            continue
        got = trimmed
        if sorted_deep(got) == sorted_deep(want):
            tally["same content, other order"] += 1
            continue
        tally["different"] += 1
        if shown < args.show:
            shown += 1
            print(f"--- different, in {case.get('path')}\n{case['query'][:400]}")
            print(f"  expected: {json.dumps(want, ensure_ascii=False)[:500]}")
            print(f"  got:      {json.dumps(got, ensure_ascii=False)[:500]}\n")

    print()
    total = sum(tally.values())
    for label, n in tally.most_common():
        print(f"{n:6d}  {label}  ({100 * n / max(1, total):.1f} %)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
