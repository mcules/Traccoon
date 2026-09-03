"""Every global a note module reaches for must exist.

`/dataview/settings` answered 500 for days because one module used
`note_settings` and never imported it. Nothing caught it: no test called that
route, and the interface takes a failing settings call in its stride, so the
only symptom was a line in a log nobody was reading.

Python does not check this at import time — a name is looked up when the line
runs, and a route that is never called never runs. So it is checked here, on the
compiled code: every global a function loads has to be something the module
knows, or a builtin. That is the whole class of mistake, not the one instance.
"""
from __future__ import annotations

import builtins
import dis
import importlib
import pathlib
import pkgutil
import types

import pytest

import app.notes

# The modules of the note area, plus the one that carries its routes. Named
# rather than found: a module that stops being imported would silently stop
# being checked, which is the opposite of what this is for.
API = ["app.api.notes_native"]


def modules() -> list[str]:
    found = list(API)
    for info in pkgutil.walk_packages(app.notes.__path__, "app.notes."):
        found.append(info.name)
    return sorted(found)


def globals_loaded(code: types.CodeType) -> set[str]:
    """Every global this code and everything nested in it reads."""
    names = {i.argval for i in dis.get_instructions(code) if i.opname == "LOAD_GLOBAL"}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= globals_loaded(const)
    return names


@pytest.mark.parametrize("name", modules())
def test_every_global_a_module_reaches_for_exists(name: str) -> None:
    module = importlib.import_module(name)
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    code = compile(source, module.__file__, "exec")
    known = set(vars(module)) | set(dir(builtins))
    missing = sorted(n for n in globals_loaded(code) if n not in known)
    assert not missing, f"{name} uses without defining: {', '.join(missing)}"
