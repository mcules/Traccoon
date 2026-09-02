#!/usr/bin/env python3
"""Record what the note workspace answers today, before it is rewritten.

The workspace speaks three little languages that live in the notes themselves:
a query language, a task filter, and the formulas of its table files. Together
they are the most expensive part of the port and the only part where "it works"
cannot be judged by looking. So the answers are recorded here, from the running
service, and the port is measured against them.

**This only works while the old service runs.** Once it is gone the recording
cannot be made again, which is why it happens first and not last.

Where the recording goes, and why not into the repository
---------------------------------------------------------
Into `data/notes-reference/`, which is not tracked. The answers are the content
of somebody's notes: names of people, of companies, appointments, money. That
does not belong in a public repository, and anonymising it faithfully enough to
stay a useful fixture is not possible: the queries return the text.

So the tests read this directory when it is there and skip when it is not.
A small set of made-up cases for CI, which would not have that problem, does not
exist yet and is its own piece of work.

Usage:
    python scripts/record_notes_reference.py            record
    python scripts/record_notes_reference.py --compare  compare against a rewrite
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import httpx

VAULT = Path(os.environ.get("VAULT_PATH", "/vault"))
# No default: the address of the workspace belongs in the environment, not in a
# public repository, and a wrong guess here would silently record nothing.
BASE = os.environ.get("NOTES_BASE_URL", "").rstrip("/")
OUT = Path(os.environ.get("NOTES_REFERENCE_DIR", "/data/notes-reference"))
HEADERS = {"Remote-User": os.environ.get("NOTES_USER", "recorder")}

# A fenced block, with the word right after the fence.
FENCE = re.compile(r"^```(dataview|dataviewjs|tasks)[ \t]*\r?\n(.*?)^```", re.M | re.S)


def blocks() -> list[dict]:
    """Every query block in the vault, with the note it sits in.

    The note matters: a query says `this.file` and means the note around it, so
    the same text in two notes is two cases, not one.
    """
    out = []
    for path in sorted(VAULT.rglob("*.md")):
        if any(p.startswith(".") for p in path.relative_to(VAULT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for kind, body in FENCE.findall(text):
            out.append({"kind": kind, "query": body,
                        "path": str(path.relative_to(VAULT))})
    return out


def tables() -> list[str]:
    return [str(p.relative_to(VAULT)) for p in sorted(VAULT.rglob("*.base"))]


# Searches worth keeping: one per operator the grammar has, so a rewrite that
# drops one is caught by name rather than by a number that got smaller.
SEARCHES = [
    "tag:#projekt", "path:03 Bereiche", "file:Daily", "line:(TODO)",
    'section:(Aufgaben)', 'task-todo:""', '"eine Wendung"', "/regex[0-9]+/",
    "tag:#projekt OR tag:#idee", "-tag:#archiv", "[status]", "content:Vostura",
]


def key(case: dict) -> str:
    """A stable name for a case, short enough to be a file name."""
    raw = json.dumps(case, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def ask(client: httpx.Client, case: dict) -> dict:
    kind = case["kind"]
    if kind == "dataview":
        r = client.post(f"{BASE}/api/dataview/query",
                        json={"query": case["query"], "path": case["path"]})
    elif kind == "dataviewjs":
        # Not run: it is JavaScript, and what it returns is a rendered page
        # rather than a value. Recorded so the port knows they exist.
        return {"skipped": "javascript"}
    elif kind == "tasks":
        r = client.post(f"{BASE}/api/dataview/tasks", json={"query": case["query"]})
    elif kind == "base":
        r = client.get(f"{BASE}/api/bases/view", params={"path": case["path"]})
    elif kind == "search":
        r = client.get(f"{BASE}/api/search", params={"q": case["query"]})
    else:
        raise ValueError(kind)
    return {"status": r.status_code, "body": r.json()}


def collect() -> list[dict]:
    cases = blocks()
    cases += [{"kind": "base", "path": p, "query": ""} for p in tables()]
    cases += [{"kind": "search", "query": q, "path": ""} for q in SEARCHES]
    return cases


def record() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = collect()
    zaehler: dict[str, int] = {}
    with httpx.Client(headers=HEADERS, timeout=60) as client:
        for case in cases:
            name = key(case)
            try:
                answer = ask(client, case)
            except httpx.HTTPError as exc:
                answer = {"error": str(exc)}
            (OUT / f"{name}.json").write_text(
                json.dumps({"case": case, "answer": answer},
                           ensure_ascii=False, indent=1, sort_keys=True),
                encoding="utf-8")
            zaehler[case["kind"]] = zaehler.get(case["kind"], 0) + 1
    print(f"recorded into {OUT}")
    for kind in sorted(zaehler):
        print(f"  {kind:12} {zaehler[kind]}")
    return 0


def compare() -> int:
    """Ask again and say what moved. This is what a rewrite is measured with."""
    if not OUT.exists():
        print(f"no recording in {OUT}", file=sys.stderr)
        return 2
    gleich = anders = fehlt = 0
    with httpx.Client(headers=HEADERS, timeout=60) as client:
        for f in sorted(OUT.glob("*.json")):
            saved = json.loads(f.read_text(encoding="utf-8"))
            try:
                now = ask(client, saved["case"])
            except httpx.HTTPError as exc:
                now = {"error": str(exc)}
            if "skipped" in saved["answer"]:
                fehlt += 1          # never ran, so it says nothing either way
            elif now == saved["answer"]:
                gleich += 1
            else:
                anders += 1
                c = saved["case"]
                where = c.get("path") or c.get("query", "")[:60]
                print(f"  differs: {c['kind']:10} {where}")
    print(f"\n{gleich} unchanged, {anders} different, {fehlt} not run")
    return 1 if anders else 0


if __name__ == "__main__":
    if not BASE:
        print("NOTES_BASE_URL is not set", file=sys.stderr)
        sys.exit(2)
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", action="store_true",
                    help="ask again and report what moved")
    sys.exit(compare() if ap.parse_args().compare else record())
