"""Templates: the two dialects, and what happens to what neither can answer."""
from __future__ import annotations

import datetime as dt

from app.notes import dates, templates as t

WHEN = dt.datetime(2026, 9, 3, 14, 5, 9)


# ------------------------------------------------------------- the date tokens

def test_the_tokens_of_this_dialect() -> None:
    """A different dialect from the query language's: `DD` is the day here and
    nothing there, `dd` the other way round. Kept apart on purpose."""
    assert dates.format_date(WHEN, "YYYY-MM-DD") == "2026-09-03"
    assert dates.format_date(WHEN, "YYYY/MM/YYYY-MM-DD") == "2026/09/2026-09-03"
    assert dates.format_date(WHEN, "HH:mm:ss") == "14:05:09"
    assert dates.format_date(WHEN, "dddd") == "Donnerstag"
    assert dates.format_date(WHEN, "D.M.YY") == "3.9.26"
    assert dates.format_date(WHEN, "A a") == "PM pm"


def test_text_in_brackets_is_taken_literally() -> None:
    """Without this a `D` in a word would become a day of the month."""
    assert dates.format_date(WHEN, "[Tag] D") == "Tag 3"
    assert dates.format_date(WHEN, "[MM]-MM") == "MM-09"


def test_the_week_number_follows_the_calendar() -> None:
    assert dates.format_date(dt.datetime(2026, 1, 1), "ww") == "01"
    assert dates.format_date(dt.datetime(2026, 12, 31), "ww") == "53"


def test_a_title_reads_back_as_the_day_it_stands_for() -> None:
    assert dates.parse_date("2026-09-03", "YYYY-MM-DD") == dt.datetime(2026, 9, 3)
    # The folders are part of the pattern of a daily note in this vault.
    assert dates.parse_date("2026/09/2026-09-03",
                            "YYYY/MM/YYYY-MM-DD") == dt.datetime(2026, 9, 3)
    # A name is matched but not interpreted: it says nothing the numbers do not.
    assert dates.parse_date("Donnerstag 2026-09-03",
                            "dddd YYYY-MM-DD") == dt.datetime(2026, 9, 3)
    assert dates.parse_date("kein Datum", "YYYY-MM-DD") is None
    assert dates.parse_date("2026-13-40", "YYYY-MM-DD") is None


# ------------------------------------------------------------- filling one in

def test_the_plain_substitutions() -> None:
    out = t.fill("# {{title}}\n{{date}} um {{time}}\n", title="Notiz", now=WHEN)
    assert out.text == "# Notiz\n2026-09-03 um 14:05\n"
    assert out.unresolved == []


def test_a_format_may_stand_in_the_token() -> None:
    assert t.fill("{{date:DD.MM.YYYY}}", title="", now=WHEN).text == "03.09.2026"


def test_the_day_a_note_is_named_for_can_be_shifted() -> None:
    """The four-argument form says "so many days after the day this note is
    named for" — the reference being the note's own title."""
    out = t.fill('<% tp.date.now("YYYY-MM-DD", 7, tp.file.title, "YYYY-MM-DD") %>',
                 title="2026-09-03", now=WHEN)
    assert out.text == "2026-09-10"
    assert out.unresolved == []


def test_what_needs_a_person_is_left_standing() -> None:
    """Mangling it would lose the content; reporting it lets the caller decide
    whether this template can be used without anybody watching."""
    raw = '<% tp.system.prompt("Wer?") %> und <% tp.file.title %>'
    out = t.fill(raw, title="Sabine", now=WHEN)
    assert out.text == '<% tp.system.prompt("Wer?") %> und Sabine'
    assert out.unresolved == ['tp.system.prompt("Wer?")']


def test_an_unreadable_reference_leaves_the_call_alone() -> None:
    out = t.fill('<% tp.date.now("YYYY-MM-DD", 1, "kein Datum", "YYYY-MM-DD") %>',
                 title="x", now=WHEN)
    assert out.text.startswith("<%")
    assert out.unresolved


# ------------------------------------------------------------- the compilation

def test_text_and_expressions_become_one_module() -> None:
    out = t.compile_template("Hallo <% tp.file.title %>!\n")
    assert "export default async function render(tp, app, host)" in out.code
    assert "tR += `Hallo `;" in out.code
    assert "tR += String(await (tp.file.title) ?? '');" in out.code
    assert out.interactive is False


def test_a_statement_block_writes_by_itself() -> None:
    out = t.compile_template("<%* tR += 'x' %>")
    assert "tR += 'x'" in out.code


def test_a_template_that_asks_says_so() -> None:
    assert t.compile_template('<% tp.system.suggester(a, b) %>').interactive is True
    assert t.compile_template('<% tp.system.prompt("?") %>').interactive is True


def test_a_backtick_in_the_text_does_not_break_the_module() -> None:
    """The text goes into a template string, so its own delimiters and `${`
    have to be escaped — otherwise the generated module does not parse."""
    out = t.compile_template("a `b` ${c} \\d")
    assert "\\`b\\`" in out.code and "\\${c}" in out.code


def test_a_dash_swallows_the_line_break_after_the_tag() -> None:
    out = t.compile_template("<% tp.file.title -%>\nweiter")
    assert "\\nweiter" not in out.code
    assert "tR += `weiter`;" in out.code


def test_the_same_template_keeps_the_same_name() -> None:
    """The browser asks for the module back by this; two compilations of one
    template must not produce two entries."""
    a = t.compile_template("gleich")
    b = t.compile_template("gleich")
    assert a.id == b.id
    assert t.compile_template("anders").id != a.id


def test_what_is_held_is_given_back_and_then_forgotten() -> None:
    kept = t.Modules()
    one = t.compile_template("eins")
    assert kept.get(kept.put(one)) == one.code
    assert kept.get("nichts dergleichen") is None


def test_the_oldest_go_when_there_are_too_many() -> None:
    kept = t.Modules()
    ids = [kept.put(t.compile_template(f"nummer {i}")) for i in range(t.KEPT + 10)]
    assert len(kept.kept) <= t.KEPT
    assert kept.get(ids[-1]) is not None
    assert kept.get(ids[0]) is None


def test_the_template_folder_is_listed_in_reading_order() -> None:
    paths = ["Vorlagen/B.md", "Vorlagen/a.md", "Vorlagen/3 Drittes.md", "Anderswo/C.md"]
    out = t.in_folder(paths, "Vorlagen")
    assert [x["name"] for x in out] == ["3 Drittes", "a", "B"]
    assert all(x["path"].startswith("Vorlagen/") for x in out)
    assert t.in_folder(paths, "") == []
