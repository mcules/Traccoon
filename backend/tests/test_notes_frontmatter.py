"""The YAML block of a note, and the three ways a 1.1 parser gets it wrong."""
from __future__ import annotations

import yaml

from app.notes.model.frontmatter import Loader, split


def test_a_note_without_a_block_is_all_body() -> None:
    daten, body = split("# Note\n\ntext\n")
    assert daten == {}
    assert body == "# Note\n\ntext\n"


def test_the_block_comes_off_the_front() -> None:
    daten, body = split("---\ntitle: Eins\ntags: [a, b]\n---\n\n# Note\n")
    assert daten == {"title": "Eins", "tags": ["a", "b"]}
    assert body == "\n# Note\n"


def test_dashes_in_the_middle_are_not_frontmatter() -> None:
    """A horizontal rule three lines down is a rule, not a block."""
    text = "# Note\n\n---\ntitle: nope\n---\n"
    daten, body = split(text)
    assert daten == {}
    assert body == text


def test_a_broken_block_does_not_make_the_note_unreadable() -> None:
    text = "---\ntitle: [unclosed\n---\n\nbody\n"
    daten, body = split(text)
    assert daten == {}
    assert body == text


def test_yes_and_no_stay_words() -> None:
    """1.1 reads these as booleans. A note saying `fertig: no` means the word."""
    daten, _ = split("---\nfertig: no\noffen: yes\nan: on\naus: off\n---\n")
    assert daten == {"fertig": "no", "offen": "yes", "an": "on", "aus": "off"}
    # And the two that are booleans in both versions still are.
    daten, _ = split("---\na: true\nb: false\n---\n")
    assert daten == {"a": True, "b": False}


def test_a_time_stays_a_time() -> None:
    """`09:00` is nine times sixty in 1.1. A vault full of times would become
    a vault full of large integers, and nothing would say so."""
    daten, _ = split("---\nzeit: 09:00\ndauer: 1:30:00\n---\n")
    assert daten == {"zeit": "09:00", "dauer": "1:30:00"}


def test_a_leading_zero_survives() -> None:
    """A phone number has been eaten by octal before."""
    daten, _ = split("---\ntelefon: 0170123456\n---\n")
    # Read as decimal, not as octal. 1.1 gives 31500078 here, which is a
    # different number that looks just as plausible in a note.
    assert daten == {"telefon": 170123456}
    assert daten["telefon"] != 0o170123456


def test_numbers_and_dates_still_work() -> None:
    daten, _ = split("---\nzahl: 42\nkomma: 3.5\nam: 2026-09-03\n---\n")
    assert daten["zahl"] == 42
    assert daten["komma"] == 3.5
    assert str(daten["am"]) == "2026-09-03"


def test_the_narrowing_did_not_leak_into_the_shared_loader() -> None:
    """The resolvers are class level and shared. Changing them in place would
    change how every other part of this application reads YAML."""
    assert yaml.safe_load("fertig: no") == {"fertig": False}
    assert yaml.load("fertig: no", Loader=Loader) == {"fertig": "no"}


def test_a_byte_order_mark_does_not_hide_the_block() -> None:
    """Files written by an editor on Windows carry one, and a fence anchored to
    the start of the string does not match past it."""
    daten, body = split("﻿---\ntags: [a]\n---\n\n# Kopf\n")
    assert daten == {"tags": ["a"]}
    assert body == "\n# Kopf\n"


def test_a_byte_order_mark_before_a_heading_is_dropped_too() -> None:
    daten, body = split("﻿# Kopf\n")
    assert daten == {}
    assert body == "# Kopf\n"


def test_a_duplicated_key_does_not_cost_the_whole_block() -> None:
    """The parser being replaced throws on a repeated key and drops everything.

    A note that says `email:` twice loses its tags, its title and every property
    with it, so it stops appearing in any query that asks for one, and nothing
    says why. Found once in this vault. Reading it is the more useful answer, and
    the last value wins, which is what a person writing the second line meant.
    """
    daten, _ = split("---\ntags: [person]\nemail: a@b\nemail: c@d\n---\n")
    assert daten["tags"] == ["person"]
    assert daten["email"] == "c@d"
