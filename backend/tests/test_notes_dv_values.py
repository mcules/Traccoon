"""Where the two languages disagree, and what a value is.

Every test here stands for a difference that produces a wrong answer rather than
an error: a string cut in the middle of an emoji, a number that rounds the other
way, a name that sorts into the wrong place. None of them would have shown up as
a failure — they show up as a table with the rows in a different order or a
price one cent off.
"""
from __future__ import annotations

import math

from app.notes.dv import js
from app.notes.dv.values import (
    compare, contains_value, date_from_string, date_to_iso, duration_from_string,
    duration_to_string, equals, format_date, link_from_string, make_link, to_str,
    truthy,
)


# ----------------------------------------------------------------- the other
# ------------------------------------------------------- language's counting

def test_a_string_is_counted_in_utf16_units() -> None:
    """An emoji is one character here and two units there, and `length()` is
    the length that language reports."""
    assert js.js_len("Termin 📅") == 9
    assert len("Termin 📅") == 8


def test_a_cut_never_leaves_half_an_emoji() -> None:
    assert js.js_slice("a📅b", 0, 2) == "a📅"
    assert js.js_slice("a📅b", 0, 1) == "a"


def test_a_whole_number_prints_without_its_point() -> None:
    assert js.js_num_str(4 / 2) == "2"
    assert js.js_num_str(2.5) == "2.5"
    assert to_str(4 / 2) == "2"


def test_the_half_rounds_up_not_to_the_even_number() -> None:
    assert js.js_round(2.5) == 3
    assert js.js_round(-2.5) == -2
    assert round(2.5) == 2                 # what Python would have said


def test_the_two_ways_of_reading_a_number_out_of_text() -> None:
    assert js.parse_float("12 Stück") == 12          # the forgiving one
    assert math.isnan(js.js_number("12 Stück"))      # the strict one
    assert js.js_number("") == 0
    assert math.isnan(js.js_number("1_000"))         # not a number there


def test_to_fixed_rounds_away_from_zero_at_the_half() -> None:
    assert js.to_fixed(1.25, 1) == "1.3"
    assert f"{1.25:.1f}" == "1.2"           # what Python's formatting says


# ------------------------------------------------------------------ sorting

def test_digits_sort_before_letters_and_by_value() -> None:
    assert js.collate("3D-Teile", "Büro") < 0
    assert js.collate("Datei 9", "Datei 10") < 0


def test_case_and_accents_do_not_separate_two_words() -> None:
    assert js.collate("Ärger", "Arger") == 0
    assert js.collate("apfel", "APFEL") == 0


def test_a_mark_sorts_before_a_letter() -> None:
    """By code point the acute accent is past `a`, by the collation it is not —
    which is the difference between `V´Kult` before and after `Valentina`."""
    assert js.collate("V´Kult", "Valentina") < 0
    assert "V´Kult" > "Valentina"           # what comparing code points says


# ---------------------------------------------------------------- the values

def test_a_link_is_taken_apart() -> None:
    link = make_link("Folder/Note#Heading|Label")
    assert link["target"] == "Folder/Note"
    assert link["subpath"] == "Heading"
    assert link["display"] == "Label"


def test_a_link_without_a_label_carries_no_empty_one() -> None:
    """An absent field is absent on the other side, not null. An explicit null
    would travel out to the interface as something somebody wrote."""
    assert "display" not in make_link("Note")
    assert "subpath" not in make_link("Note")


def test_only_a_string_that_is_nothing_but_a_link_is_one() -> None:
    assert link_from_string("[[Note]]") is not None
    assert link_from_string("see [[Note]] there") is None


def test_a_bare_name_and_a_path_are_the_same_note() -> None:
    assert equals(make_link("Note"), make_link("Folder/Note"))
    assert not equals(make_link("A/Note"), make_link("B/Note"))


def test_a_day_survives_being_written_out_and_read_back() -> None:
    for text in ("2026-09-02", "2026-09-02T07:30:00"):
        assert date_to_iso(date_from_string(text)) == text


def test_a_span_reads_as_its_two_largest_units() -> None:
    assert duration_to_string(duration_from_string("1 day 1 hour 30 minutes")) \
        == "1 day, 1 hour"
    assert duration_from_string("2 weeks")["ms"] == 14 * 86_400_000


def test_the_weekday_names_start_on_sunday() -> None:
    """That language counts Sunday as zero. Taking Python's numbering would
    shift every weekday name in every table by one day."""
    assert format_date(date_from_string("2026-09-02"), "EEEE") == "Wednesday"


def test_nothing_sorts_last_and_stays_last() -> None:
    assert compare(None, "a") > 0
    assert compare("a", None) < 0
    assert compare(None, None) == 0


def test_contains_asks_a_list_about_membership_and_text_about_a_part() -> None:
    assert contains_value(["#a", "#b"], "#a")
    assert contains_value("Hallo Welt", "welt")
    assert not contains_value(None, "x")


def test_empty_things_are_false() -> None:
    assert not truthy("")
    assert not truthy([])
    assert not truthy(0)
    assert truthy("0")                      # a non-empty string, so true
