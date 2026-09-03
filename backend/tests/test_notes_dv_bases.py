"""A table file: read, filtered, computed, summed."""
from __future__ import annotations

import pytest

from app.notes.dv.bases import run as bases
from app.notes.dv.bases.parse import normalize_id, parse
from app.notes.dv.pages import PageIndex

STAT = {"size": 10, "ctimeMs": 1_700_000_000_000, "mtimeMs": 1_700_000_000_000}

NOTES = {
    "Lager/Zelt.md": "---\nlagerort: Keller\npreis: 120\neingepackt: true\ntags: [ausruestung]\n---\n",
    "Lager/Kocher.md": "---\nlagerort: Keller\npreis: 40\neingepackt: false\ntags: [ausruestung]\n---\n",
    "Lager/Karte.md": "---\nlagerort: Büro\neingepackt: true\ntags: [ausruestung]\n---\n",
    "Anderes/Notiz.md": "# Nur Text\n",
}

FILE = """
filters:
  and:
    - file.hasTag("ausruestung")
formulas:
  brutto: 'note.preis * 1.19'
properties:
  note.lagerort:
    displayName: Ort
views:
  - type: table
    name: Packliste
    order: [file.basename, note.lagerort, note.preis, note.eingepackt, formula.brutto]
    sort:
      - property: file.basename
        direction: ASC
    summaries:
      note.preis: sum
      note.eingepackt: checked
"""


@pytest.fixture()
def index() -> PageIndex:
    idx = PageIndex()
    idx.build([(rel, text, STAT) for rel, text in NOTES.items()])
    return idx


def test_a_bare_name_means_the_notes_own_property() -> None:
    assert normalize_id("status") == "note.status"
    assert normalize_id("file.name") == "file.name"


def test_an_empty_file_is_a_table_over_everything() -> None:
    config = parse("")
    assert len(config.views) == 1
    assert config.views[0].type == "table"


def test_a_filter_narrows_the_rows(index) -> None:
    out = bases.run(index, FILE)
    assert [r["path"] for r in out["rows"]] == [
        "Lager/Karte.md", "Lager/Kocher.md", "Lager/Zelt.md"]


def test_a_column_takes_the_name_the_file_gives_it(index) -> None:
    out = bases.run(index, FILE)
    assert [c["label"] for c in out["columns"]] == [
        "Basename", "Ort", "Preis", "Eingepackt", "Brutto"]


def test_a_formula_is_worked_out_per_note(index) -> None:
    out = bases.run(index, FILE)
    values = {r["path"]: r["values"]["formula.brutto"] for r in out["rows"]}
    assert values["Lager/Zelt.md"] == pytest.approx(142.8)
    assert values["Lager/Karte.md"] is None       # no price, so nothing


def test_summaries_count_and_add_up(index) -> None:
    out = bases.run(index, FILE)
    assert out["summaries"]["note.preis"] == "160"
    assert out["summaries"]["note.eingepackt"] == "2 / 3"


def test_a_formula_that_needs_itself_gives_nothing_rather_than_running_on(index) -> None:
    out = bases.run(index, "formulas:\n  a: 'formula.b'\n  b: 'formula.a'\n"
                           "views:\n  - {type: table, name: T, order: [formula.a]}\n")
    assert all(r["values"]["formula.a"] is None for r in out["rows"])


def test_a_comparison_against_a_missing_value_is_false(index) -> None:
    """A note without a price is not "cheaper than 10" — it is simply not in
    the answer."""
    out = bases.run(index, 'filters: "note.preis < 1000"\n')
    assert {r["path"] for r in out["rows"]} == {"Lager/Kocher.md", "Lager/Zelt.md"}


def test_a_summary_over_a_column_the_view_hides_counts_nothing(index) -> None:
    """Only the columns a view shows are worked out per note, so a summary on a
    hidden one adds up nothing. Copied rather than fixed: computing extra
    columns for it would change what the file costs to open, and the file can
    say what it wants shown."""
    out = bases.run(index, "views:\n  - type: table\n    name: T\n"
                           "    order: [file.basename]\n"
                           "    summaries: {note.preis: sum}\n")
    assert out["summaries"]["note.preis"] == "0"


def test_a_broken_filter_is_named_once_and_not_guessed_at(index) -> None:
    out = bases.run(index, 'filters: "note.preis <"\n')
    assert out["rows"] == []
    assert len(out["errors"]) == 1


def test_a_tag_stands_for_everything_under_it(index) -> None:
    index.build([("A.md", "---\ntags: [projekt/afu]\n---\n", STAT)])
    out = bases.run(index, 'filters: \'file.hasTag("projekt")\'\n')
    assert [r["path"] for r in out["rows"]] == ["A.md"]


def test_the_limit_cuts_the_top_of_the_sorted_list(index) -> None:
    out = bases.run(index, "views:\n  - type: table\n    name: T\n"
                           "    order: [file.basename]\n"
                           "    sort: [{property: file.basename, direction: DESC}]\n"
                           "    limit: 2\n")
    assert [r["values"]["file.basename"] for r in out["rows"]] == ["Zelt", "Notiz"]
    assert out["matched"] == 4 and out["total"] == 2
