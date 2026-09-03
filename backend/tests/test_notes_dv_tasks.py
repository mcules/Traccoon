"""The task filter: one line per instruction, and each line on its own."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.notes.dv import settings as plugin_settings
from app.notes.dv import tasks as dv_tasks
from app.notes.dv.pages import PageIndex

STAT = {"size": 10, "ctimeMs": 1_700_000_000_000, "mtimeMs": 1_700_000_000_000}


def _day(offset: int) -> str:
    n = datetime.now() + timedelta(days=offset)
    return f"{n.year}-{n.month:02d}-{n.day:02d}"


@pytest.fixture()
def index() -> PageIndex:
    plugin_settings.reset()
    plugin_settings._statuses = plugin_settings.CORE_STATUSES + [
        {"symbol": "/", "name": "In Progress", "nextStatusSymbol": "x", "type": "IN_PROGRESS"},
        {"symbol": "-", "name": "Cancelled", "nextStatusSymbol": " ", "type": "CANCELLED"},
    ]
    idx = PageIndex()
    idx.build([
        ("02 Projekte/Alpha.md",
         "## Offen\n\n"
         f"- [ ] Dach streichen 📅 {_day(3)} #haus ⏫\n"
         f"- [ ] Keller aufräumen 📅 {_day(-3)} #haus\n"
         "- [/] läuft schon\n"
         "- [-] abgesagt\n"
         "- [x] fertig ✅ 2026-01-01\n"
         "- [ ] ohne Datum\n", STAT),
        ("03 Firmen/Acme.md", "- [ ] Rechnung prüfen #firma\n", STAT),
    ])
    yield idx
    plugin_settings.reset()


def texts(result: dict) -> list[str]:
    return [dv_tasks.task_description(t) for g in result["groups"] for t in g["tasks"]]


# ------------------------------------------------------------------ filters

def test_not_done_leaves_out_what_is_cancelled(index) -> None:
    """The configured status types decide it, not the character in the box: a
    cancelled task is not "not done", and one in progress is."""
    out = texts(dv_tasks.execute(index, "not done"))
    assert "läuft schon" in out
    assert "abgesagt" not in out
    assert "fertig" not in out


def test_a_status_can_be_asked_about_by_type(index) -> None:
    assert texts(dv_tasks.execute(index, "status.type is IN_PROGRESS")) == ["läuft schon"]


def test_dates_can_be_asked_about_in_words(index) -> None:
    assert texts(dv_tasks.execute(index, "due before today")) == ["Keller aufräumen #haus"]
    assert texts(dv_tasks.execute(index, "due after today")) == ["Dach streichen #haus"]
    assert len(texts(dv_tasks.execute(index, "no due date"))) == 5


def test_priority_is_asked_about_by_rank(index) -> None:
    assert texts(dv_tasks.execute(index, "priority is above medium")) == ["Dach streichen #haus"]


def test_text_fields_are_asked_about_by_part_or_by_pattern(index) -> None:
    assert texts(dv_tasks.execute(index, "description includes keller")) == ["Keller aufräumen #haus"]
    assert texts(dv_tasks.execute(index, "path does not include 03 Firmen")) != []
    assert texts(dv_tasks.execute(index, r"description regex matches /^Dach/")) == \
        ["Dach streichen #haus"]


def test_the_combinators_hold_a_line_together(index) -> None:
    out = texts(dv_tasks.execute(index, "(tag includes #haus) AND (priority is above medium)"))
    assert out == ["Dach streichen #haus"]
    both = texts(dv_tasks.execute(index, "(tag includes #haus) OR (tag includes #firma)"))
    assert len(both) == 3


def test_an_instruction_nobody_understands_is_said_out_loud(index) -> None:
    """Refusing the whole block over one mistyped line would hide the tasks the
    other lines found; answering silently would hide the line."""
    out = dv_tasks.execute(index, "not done\nsortiert nach irgendwas")
    assert out["warnings"] == ["sortiert nach irgendwas"]
    assert out["total"] > 0


# --------------------------------------------------------- sort, group, cut

def test_sorting_puts_the_undated_last(index) -> None:
    out = texts(dv_tasks.execute(index, "not done\nsort by due"))
    assert out[:2] == ["Keller aufräumen #haus", "Dach streichen #haus"]
    assert set(out[2:]) == {"läuft schon", "ohne Datum", "Rechnung prüfen #firma"}


def test_grouping_names_the_group_and_links_it(index) -> None:
    out = dv_tasks.execute(index, "not done\ngroup by filename")
    assert [g["key"] for g in out["groups"]] == ["Acme", "Alpha"]
    assert out["groups"][0]["link"] == "03 Firmen/Acme.md"


def test_the_count_is_the_one_before_the_cut(index) -> None:
    """`total` is what the filter found; `limit` only shortens what is shown."""
    out = dv_tasks.execute(index, "not done\nlimit 2")
    assert out["total"] == 5
    assert len(out["groups"][0]["tasks"]) == 2


def test_a_layout_switch_is_not_a_filter(index) -> None:
    out = dv_tasks.execute(index, "not done\nhide backlink\nshort mode")
    assert out["layout"]["hideBacklink"] is True
    assert out["layout"]["shortMode"] is True
    assert out["warnings"] == []


# ------------------------------------------------------------ descriptions

def test_the_description_drops_the_marks_and_the_block_id() -> None:
    task = {"text": "Etwas tun 📅 2026-09-02 ⏫ #tag ^abc-1", "tags": []}
    assert dv_tasks.task_description(task) == "Etwas tun #tag"


def test_a_date_in_words_reads_as_a_day() -> None:
    assert dv_tasks.parse_date_expr("2026-09-02")["hasTime"] is False
    assert dv_tasks.parse_date_expr("in 7 days") is not None
    assert dv_tasks.parse_date_expr("3 days ago") is not None
    assert dv_tasks.parse_date_expr("übermorgen") is None
