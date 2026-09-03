"""The migrations have to build the schema the models describe.

Two ways lead to a database here: the backend creates what is missing from the
models when it starts, and the migrations do it step by step. Only the first one
was ever exercised, so the second fell behind quietly — seventeen tables and
forty-odd columns were missing from it, and `alembic upgrade head` on an empty
database had never once reached the end.

This compares the two without needing a database: every table and column the
models define has to be reachable through the chain.
"""
from __future__ import annotations

import re
from pathlib import Path

import app.models  # noqa: F401  (registers every table)
from app.db import Base

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def revisions() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in VERSIONS.glob("*.py")]


def test_every_table_of_the_models_is_created_somewhere() -> None:
    made: set[str] = set()
    for src in revisions():
        made |= set(re.findall(r"op\.create_table\(\s*['\"]([^'\"]+)", src))
        made |= set(re.findall(r"op\.rename_table\([^,]+,\s*['\"]([^'\"]+)", src))
    missing = sorted(set(Base.metadata.tables) - made)
    assert not missing, f"no revision creates: {', '.join(missing)}"


def test_the_chain_has_one_beginning_and_one_end() -> None:
    """Two heads mean two versions of the truth, and which one a database ends
    up with depends on the order it was upgraded in. A fork in the middle is
    fine as long as it comes back together — that is what a merge revision is
    for, and this vault's chain has one."""
    down: dict[str, tuple[str, ...]] = {}
    for src in revisions():
        rev = re.search(r"^revision\s*=\s*['\"]([^'\"]+)", src, re.M).group(1)
        raw = re.search(r"^down_revision\s*=\s*(.+)$", src, re.M).group(1).strip()
        down[rev] = tuple(re.findall(r"['\"]([^'\"]+)['\"]", raw))
    parents = {p for ps in down.values() for p in ps}
    roots = [r for r, ps in down.items() if not ps]
    assert len(roots) == 1, f"more than one beginning: {roots}"
    heads = sorted(set(down) - parents)
    assert len(heads) == 1, f"more than one head: {heads}"


def test_a_type_is_not_created_twice() -> None:
    """A table that uses an enum the revision has just created explicitly tries
    to create it a second time, and the whole chain stops there."""
    for path in VERSIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for name in re.findall(r"^(\w+)\s*=\s*sa\.Enum\(", src, re.M):
            created = re.search(rf"\b{name}\.create\(", src) or re.search(
                rf"for e in \([^)]*\b{name}\b", src)
            used = re.search(rf"sa\.Column\([^)]*,\s*{name}\b", src)
            assert not (created and used), (
                f"{path.name}: {name} is created explicitly and used in a table — "
                "declare it with postgresql.ENUM(..., create_type=False)")


def test_an_index_is_not_created_twice() -> None:
    """`index=True` on a column already creates it; a `create_index` for the
    same name afterwards is the same failure one line further down."""
    for path in VERSIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"op\.create_table\(\s*['\"]([^'\"]+)['\"],([\s\S]*?)\n    \)", src):
            table, body = m.group(1), m.group(2)
            for col in re.findall(r"sa\.Column\(\s*['\"]([^'\"]+)['\"][^\n]*index=True", body):
                name = f"ix_{table}_{col}"
                assert not re.search(rf"op\.create_index\(\s*['\"]{re.escape(name)}['\"]", src), (
                    f"{path.name}: {name} is created twice")
