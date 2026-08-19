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

from app.core.fehler import Fehler

from conftest import auth, make_user

API = pathlib.Path(__file__).resolve().parent.parent / "app" / "api"


def _aufrufe():
    """Every Fehler(...) in the API: file, line, key, text, parameter names."""
    for p in sorted(API.rglob("*.py")):
        quelle = p.read_text(encoding="utf-8")
        for k in ast.walk(ast.parse(quelle)):
            if isinstance(k, ast.Call) and getattr(k.func, "id", "") == "Fehler":
                yield p.name, k.lineno, k.args, [w.arg for w in k.keywords]


def test_platzhalter_und_werte_passen_zusammen():
    """A text asks for {name}, so the call has to hand a `name` over. The other way round as
    well: an unused value would silently fall out of the sentence."""
    fehler = []
    for datei, zeile, args, namen in _aufrufe():
        key, text = args[1], args[2]
        assert isinstance(key, ast.Constant) and key.value.startswith("err."), f"{datei}:{zeile}"
        assert isinstance(text, ast.Constant), f"{datei}:{zeile}: text is not a literal"
        platzhalter = set(re.findall(r"\{(\w+)\}", text.value))
        if platzhalter != set(namen):
            fehler.append(f"{datei}:{zeile}: {sorted(platzhalter)} != {sorted(namen)}")
    assert not fehler, "\n".join(fehler)


def test_gleicher_schluessel_gleicher_text():
    """One key, one sentence. Two wordings under one key would make the translation a lottery
    depending on which endpoint answered."""
    gesehen: dict[str, str] = {}
    fehler = []
    for datei, zeile, args, _namen in _aufrufe():
        key, text = args[1].value, args[2].value
        if gesehen.setdefault(key, text) != text:
            fehler.append(f"{datei}:{zeile}: {key} says two things")
    assert not fehler, "\n".join(fehler)


def test_werte_landen_im_text():
    f = Fehler(status.HTTP_404_NOT_FOUND, "err.test", "No {was} for {wer}", was="board", wer="anna")
    assert f.detail == "No board for anna"
    assert f.werte == {"was": "board", "wer": "anna"}


async def test_antwort_nennt_den_schluessel(client, db):
    """What the browser needs: the sentence and the name of the sentence."""
    anna = await make_user(db, "anna")
    r = await client.get("/issues/999999", headers=auth(anna))
    assert r.status_code == 404
    assert r.json() == {"detail": "Ticket not found", "key": "err.ticket_not_found"}
