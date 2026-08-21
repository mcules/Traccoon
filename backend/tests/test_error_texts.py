"""The error texts of the API: filled in, named, and consistent in themselves.

The browser shows a German sentence for a German interface, and it can only do that when the
server names the text it is sending. What the catalog on the other side knows is checked in
the frontend (`tools/fehlertexte-check.mjs`); here stands what is provable without it.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest
from fastapi import status

from app.core.error import Fehler

from conftest import auth, make_user

API = pathlib.Path(__file__).resolve().parent.parent / "app" / "api"


def _aufrufe():
    """Every Fehler(...) in the API: file, line, key, text, parameter names."""
    for p in sorted(API.rglob("*.py")):
        source = p.read_text(encoding="utf-8")
        for k in ast.walk(ast.parse(source)):
            if isinstance(k, ast.Call) and getattr(k.func, "id", "") == "Fehler":
                yield p.name, k.lineno, k.args, [w.arg for w in k.keywords]


def test_placeholders_and_values_match():
    """A text asks for {name}, so the call has to hand a `name` over. The other way round as
    well: an unused value would silently fall out of the sentence."""
    error = []
    for file, line, args, namen in _aufrufe():
        key, text = args[1], args[2]
        assert isinstance(key, ast.Constant) and key.value.startswith("err."), f"{file}:{line}"
        assert isinstance(text, ast.Constant), f"{file}:{line}: text is not a literal"
        platzhalter = set(re.findall(r"\{(\w+)\}", text.value))
        if platzhalter != set(namen):
            error.append(f"{file}:{line}: {sorted(platzhalter)} != {sorted(namen)}")
    assert not error, "\n".join(error)


def test_same_key_same_text():
    """One key, one sentence. Two wordings under one key would make the translation a lottery
    depending on which endpoint answered."""
    seen: dict[str, str] = {}
    error = []
    for file, line, args, _namen in _aufrufe():
        key, text = args[1].value, args[2].value
        if seen.setdefault(key, text) != text:
            error.append(f"{file}:{line}: {key} says two things")
    assert not error, "\n".join(error)


def test_values_land_in_the_text():
    f = Fehler(status.HTTP_404_NOT_FOUND, "err.test", "No {was} for {wer}", was="board", wer="anna")
    assert f.detail == "No board for anna"
    assert f.values == {"was": "board", "wer": "anna"}


async def test_the_answer_names_the_key(client, db):
    """What the browser needs: the sentence and the name of the sentence."""
    anna = await make_user(db, "anna")
    r = await client.get("/issues/999999", headers=auth(anna))
    assert r.status_code == 404
    assert r.json() == {"detail": "Ticket not found", "key": "err.ticket_not_found"}
