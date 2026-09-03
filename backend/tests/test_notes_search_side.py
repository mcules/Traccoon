"""What a search found and where, the properties in use, and the picture."""
from __future__ import annotations

import datetime as dt

from app.notes import properties as pr
from app.notes.index.links import LinkGraph
from app.notes import graph as gr
from app.notes.query import matches as mt


# ------------------------------------------------------------ where it stands

def test_a_hit_is_shown_with_what_is_around_it() -> None:
    count, contexts = mt.in_body("Ein Kalender voller Termine.", ["kalender"])
    assert count == 1
    assert contexts[0].text == "Ein Kalender voller Termine."
    assert contexts[0].ranges == [[4, 8]]
    assert contexts[0].pre is False and contexts[0].post is False


def test_two_hits_close_together_share_one_window() -> None:
    count, contexts = mt.in_body("Kalender und Kalender", ["kalender"])
    assert count == 2 and len(contexts) == 1
    assert contexts[0].ranges == [[0, 8], [13, 8]]


def test_two_hits_far_apart_get_a_window_each() -> None:
    body = "Kalender" + ("x" * 200) + "Kalender"
    count, contexts = mt.in_body(body, ["kalender"])
    assert count == 2 and len(contexts) == 2
    assert contexts[0].post is True and contexts[1].pre is True


def test_the_offsets_are_the_ones_a_browser_counts() -> None:
    """They go to a page that puts a mark around a stretch of text, and that
    page counts UTF-16 code units: an emoji is two there and one in Python.
    Counting in characters shifts every mark after it by one."""
    _, contexts = mt.in_body("🩺 Kalender", ["kalender"])
    assert contexts[0].ranges == [[3, 8]]        # not [[2, 8]]


def test_the_window_is_measured_the_same_way() -> None:
    """The padding around a hit is counted in those units too — otherwise the
    window starts a letter late once an emoji stands before it."""
    body = "🩺" * 40 + "Kalender"
    _, contexts = mt.in_body(body, ["kalender"])
    # 32 units of padding is 16 emoji, so the window holds those plus the word.
    assert contexts[0].text.startswith("🩺" * 16)
    assert contexts[0].text.endswith("Kalender")


def test_an_overlapping_hit_is_counted_once() -> None:
    count, _ = mt.in_body("Kalenderblatt", ["kalender", "kalenderblatt"])
    assert count == 1


def test_a_word_of_one_letter_is_not_looked_for() -> None:
    assert mt.terms_of_query("a Kalender") == ["Kalender"]


def test_the_field_parts_of_a_query_are_not_words_to_mark() -> None:
    assert mt.terms_of_query("tag:#idee Kalender") == ["Kalender"]


def test_nothing_to_look_for_finds_nothing() -> None:
    assert mt.in_body("irgendwas", []) == (0, [])
    assert mt.in_body("irgendwas", ["fehlt"]) == (0, [])


# ---------------------------------------------------------------- properties

def test_what_kind_of_property_this_is() -> None:
    assert pr.type_of("irgendwas", "Text") == "text"
    assert pr.type_of("irgendwas", 3) == "number"
    assert pr.type_of("irgendwas", True) == "checkbox"
    assert pr.type_of("irgendwas", ["a", "b"]) == "list"
    assert pr.type_of("irgendwas", "2026-09-03") == "date"
    assert pr.type_of("irgendwas", "2026-09-03T10:00") == "datetime"


def test_a_real_date_is_a_date() -> None:
    """A YAML reader hands back a date object here rather than a string. The
    side this replaces only looks at strings, so it calls fourteen notes' pickup
    date plain text."""
    assert pr.type_of("abholdatum", dt.date(2026, 9, 3)) == "date"
    assert pr.type_of("wann", dt.datetime(2026, 9, 3, 10)) == "datetime"


def test_the_three_built_in_ones_are_always_lists() -> None:
    assert pr.type_of("tags", "eins") == "list"
    assert pr.type_of("aliases", "eins") == "list"


def test_the_most_used_type_wins_and_the_most_used_key_leads() -> None:
    out = pr.in_use([{"a": 1}, {"a": 2}, {"a": "x"}, {"b": "y"}])
    by_key = {p["key"]: p for p in out}
    assert by_key["a"] == {"key": "a", "type": "number", "count": 3}
    assert by_key["b"]["count"] == 1
    assert [p["key"] for p in out][:1] == ["a"]


def test_the_built_in_ones_are_offered_even_when_unused() -> None:
    keys = {p["key"] for p in pr.in_use([])}
    assert {"tags", "aliases", "cssclasses"} <= keys


def test_types_set_in_the_vault_are_read_and_written(tmp_path) -> None:
    """The file belongs to the vault: something else wrote it and will read it
    again, so everything in it that is not this one key stays as it was."""
    (tmp_path / ".conf").mkdir()
    (tmp_path / ".conf" / "types.json").write_text(
        '{"anderes": 1, "types": {"a": "text"}}', encoding="utf-8")
    assert pr.assigned(tmp_path, ".conf") == {"a": "text"}
    assert pr.assign(tmp_path, ".conf", "b", "date") == {"a": "text", "b": "date"}
    import json
    again = json.loads((tmp_path / ".conf" / "types.json").read_text(encoding="utf-8"))
    assert again["anderes"] == 1


def test_a_vault_without_the_file_simply_has_none(tmp_path) -> None:
    assert pr.assigned(tmp_path, ".conf") == {}
    assert pr.assigned(tmp_path, "") == {}


# --------------------------------------------------------------- the picture

def make_graph() -> LinkGraph:
    g = LinkGraph()
    g.outgoing = {"Eins.md": set(), "Zwei.md": set()}
    g.key_to_path = {"eins": "Eins.md", "zwei": "Zwei.md"}
    g.raw_links = {"Eins.md": ["Zwei", "Bild.png", "Nie geschrieben", "Eins"]}
    g.tags = {"Eins.md": ["idee"], "Zwei.md": []}
    return g


def test_every_note_is_a_dot() -> None:
    out = gr.build(make_graph())
    notes = [n for n in out["nodes"] if n["kind"] == "note"]
    assert {n["id"] for n in notes} == {"Eins.md", "Zwei.md"}
    assert [n["label"] for n in notes if n["id"] == "Eins.md"] == ["Eins"]


def test_a_link_to_nothing_is_a_dot_too() -> None:
    """Not an error to hide: it says a note was meant and never written."""
    out = gr.build(make_graph())
    missing = [n for n in out["nodes"] if n["kind"] == "unresolved"]
    assert [n["label"] for n in missing] == ["Nie geschrieben"]


def test_an_embedded_file_is_not_a_note_somebody_forgot() -> None:
    out = gr.build(make_graph())
    files = [n for n in out["nodes"] if n["kind"] == "attachment"]
    assert [n["label"] for n in files] == ["Bild.png"]


def test_a_note_pointing_at_itself_is_no_line() -> None:
    out = gr.build(make_graph())
    assert all(e["source"] != e["target"] for e in out["edges"])
    assert ("Eins.md", "Zwei.md") in {(e["source"], e["target"]) for e in out["edges"]}
