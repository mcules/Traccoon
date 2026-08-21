#!/usr/bin/env python3
"""Rename German identifiers to English — safely.

Why a tool and not sed: an identifier and a string that happens to read the same must not
share a fate. `series` as a variable may be renamed; `"reihe"` as a dictionary key in a
stored graph may not, because that key lives in the database.

So the rewrite runs over the syntax tree: only names, attributes, arguments and keyword
arguments are touched, never string literals, never comments (those are translated
separately, by hand, because a comment is prose).

    python tools/rename_identifiers.py --check backend/app     # what would change
    python tools/rename_identifiers.py --write backend/app     # do it
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

WOERTERBUCH = json.loads((Path(__file__).parent / "eindeutschen.json").read_text())["stamm"]

# Names that must not move, whatever they look like: they are keys in the database, in the
# API or in stored graphs, and a rename here would break data, not code.
TABU = {
    "Fehler",           # exception class, renamed separately with its call sites
    "reihe", "punkt",   # only as *strings* — the AST pass never sees those, listed for the eye
}


# Names Python already uses. A rename onto one of these compiles fine and then fails at
# runtime in a way that reads like a logic bug: `liste` became `list`, and the built-in was
# gone inside that function.
BUILTINS = set(dir(__builtins__) if isinstance(__builtins__, dict) is False else __builtins__)
BUILTINS |= {"list", "dict", "set", "type", "id", "filter", "map", "next", "all", "any",
             "sum", "min", "max", "len", "str", "int", "float", "bool", "bytes", "tuple",
             "format", "input", "object", "property", "range", "round", "hash", "help",
             "open", "print", "vars", "iter", "zip", "dir", "eval", "exec", "hex", "oct",
             "abs", "bin", "chr", "ord", "repr", "slice", "sorted", "super", "time"}


def _neuer_name(name: str) -> str | None:
    """The English name, or None when nothing matches."""
    if name in TABU or name.startswith("__"):
        return None
    fuehrend = len(name) - len(name.lstrip("_"))
    kern = name[fuehrend:]
    if not kern:
        return None

    # camelCase and PascalCase keep their shape, snake_case keeps its underscores.
    if "_" in kern or kern.islower() or kern.isupper():
        teile = kern.split("_")
        neu = [WOERTERBUCH.get(t.lower(), t) for t in teile]
        if all(a.lower() == b.lower() for a, b in zip(teile, neu)):
            return None
        gebaut = "_".join(neu)
        if kern.isupper():
            gebaut = gebaut.upper()
        if gebaut in BUILTINS:
            return None
        return "_" * fuehrend + gebaut

    # PascalCase: split on capitals
    stuecke = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", kern)
    neu = [WOERTERBUCH.get(s.lower(), s) for s in stuecke]
    if all(a.lower() == b.lower() for a, b in zip(stuecke, neu)):
        return None
    return "_" * fuehrend + "".join(w[:1].upper() + w[1:] for w in neu)


class Sammler(ast.NodeVisitor):
    """Collect every identifier the file defines or uses."""

    def __init__(self) -> None:
        self.namen: set[str] = set()

    def visit_FunctionDef(self, n):  # noqa: N802
        self.namen.add(n.name); self.generic_visit(n)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, n):  # noqa: N802
        self.namen.add(n.name); self.generic_visit(n)

    def visit_Name(self, n):  # noqa: N802
        self.namen.add(n.id); self.generic_visit(n)

    def visit_arg(self, n):  # noqa: N802
        self.namen.add(n.arg); self.generic_visit(n)

    def visit_keyword(self, n):  # noqa: N802
        if n.arg:
            self.namen.add(n.arg)
        self.generic_visit(n)

    def visit_Attribute(self, n):  # noqa: N802
        self.namen.add(n.attr); self.generic_visit(n)

    def visit_ImportFrom(self, n):  # noqa: N802
        # `from .compaction import uebergabe` — ohne diesen Zweig wird die Funktion drueben
        # umbenannt und der Import zeigt danach auf einen Namen, den es nicht mehr gibt.
        for a in n.names:
            self.namen.add(a.name)
            if a.asname:
                self.namen.add(a.asname)
        self.generic_visit(n)

    def visit_Import(self, n):  # noqa: N802
        for a in n.names:
            if a.asname:
                self.namen.add(a.asname)
        self.generic_visit(n)


def _umbenennungen(quelle: str) -> dict[str, str]:
    baum = ast.parse(quelle)
    s = Sammler()
    s.visit(baum)
    aus = {}
    for name in s.namen:
        neu = _neuer_name(name)
        if neu and neu != name:
            aus[name] = neu
    return aus


def bearbeite(datei: Path, schreiben: bool) -> tuple[int, dict[str, str]]:
    quelle = datei.read_text(encoding="utf-8")
    try:
        karte = _umbenennungen(quelle)
    except SyntaxError as exc:
        print(f"  ÜBERSPRUNGEN {datei}: {exc}", file=sys.stderr)
        return 0, {}
    if not karte:
        return 0, {}

    # Word-wise over the text, but only outside strings and comments. Tokenizing keeps a
    # `"wert"` inside a string apart from a `wert` that is a variable.
    import io
    import tokenize

    stuecke: list[str] = []
    treffer = 0
    for tok in tokenize.generate_tokens(io.StringIO(quelle).readline):
        if tok.type == tokenize.NAME and tok.string in karte:
            stuecke.append((tok.start, tok.end, karte[tok.string]))
            treffer += 1

    if not treffer:
        return 0, {}
    if not schreiben:
        return treffer, karte

    zeilen = quelle.splitlines(keepends=True)
    for (zs, zc), (es, ec), neu in reversed(stuecke):
        z = zeilen[zs - 1]
        zeilen[zs - 1] = z[:zc] + neu + z[ec:]
    neu_quelle = "".join(zeilen)
    neu_quelle = _namen_in_strings(neu_quelle, karte)
    ast.parse(neu_quelle)          # must still be valid Python
    datei.write_text(neu_quelle, encoding="utf-8")
    return treffer, karte


# Places where an identifier travels as a *string* and therefore survived the token pass.
# `monkeypatch.setattr(mod, "aufrufen", …)` binds by name at runtime: rename the function and
# leave the string, and the patch silently attaches to nothing.
_STRING_STELLEN = re.compile(
    r"((?:monkeypatch\.)?(?:setattr|getattr|hasattr|delattr)\(\s*[^,()]+,\s*"
    r"|patch\.object\(\s*[^,()]+,\s*"
    r"|parametrize\(\s*)"
    r"(['\"])([A-Za-z_][A-Za-z0-9_,]*)\2")


def _namen_in_strings(quelle: str, karte: dict[str, str]) -> str:
    def eine(m: re.Match) -> str:
        namen = [n.strip() for n in m.group(3).split(",")]
        neu = [karte.get(n, n) for n in namen]
        return m.group(1) + m.group(2) + ",".join(neu) + m.group(2)
    return _STRING_STELLEN.sub(eine, quelle)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("pfade", nargs="+")
    p.add_argument("--write", action="store_true")
    p.add_argument("--check", action="store_true")
    args = p.parse_args()

    gesamt = 0
    alle: dict[str, str] = {}
    for roh in args.pfade:
        wurzel = Path(roh)
        dateien = sorted(wurzel.rglob("*.py")) if wurzel.is_dir() else [wurzel]
        for f in dateien:
            n, karte = bearbeite(f, args.write)
            if n:
                gesamt += n
                alle.update(karte)
                print(f"  {n:4} {f}")
    print(f"\n{gesamt} Vorkommen, {len(alle)} verschiedene Namen")
    if args.check:
        for a, b in sorted(alle.items())[:40]:
            print(f"    {a} -> {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
