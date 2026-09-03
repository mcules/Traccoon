"""The word road of the search, and the switch that chooses it."""
from __future__ import annotations

import pytest

from app.notes.query.evaluate import Doc
from app.notes.query.words import WordIndex, terms_of, uses_operators


@pytest.mark.parametrize("query", [
    'tag:idee', 'path:x', '"eine Wendung"', '/regex/', '(a b)', '[status]',
    'a -b', 'a OR b', '-a',
])
def test_a_query_that_uses_the_language_takes_the_other_road(query: str) -> None:
    assert uses_operators(query) is True


@pytest.mark.parametrize("query", ["hallo", "zwei wörter", "task", "OReilly", "a-b"])
def test_plain_words_take_this_one(query: str) -> None:
    assert uses_operators(query) is False


def test_words_are_split_the_way_german_is_written() -> None:
    """Splitting on ASCII alone would make two words out of one."""
    assert terms_of("Größe über alles") == ["größe", "über", "alles"]
    assert terms_of("e-mail's Wert") == ["e-mail's", "wert"]


@pytest.fixture()
def index() -> WordIndex:
    docs = {
        "Schreibstil.md": Doc(path="Schreibstil.md", filename="Schreibstil.md",
                              content="Locker technisch und direkt", tags=["kontext"]),
        "Anderes.md": Doc(path="Anderes.md", filename="Anderes.md",
                          content="Hier steht Schreibstil nur im Text", tags=[]),
        "Drittes.md": Doc(path="Drittes.md", filename="Drittes.md",
                          content="nichts davon", tags=["schreibstil"]),
    }
    i = WordIndex()
    i.build(docs, {"Schreibstil.md": ["Der Ton"]})
    return i


def test_a_word_finds_the_notes_that_carry_it(index: WordIndex) -> None:
    treffer = {rel for rel, _ in index.search("Schreibstil")}
    assert treffer == {"Schreibstil.md", "Anderes.md", "Drittes.md"}


def test_the_title_weighs_more_than_the_body(index: WordIndex) -> None:
    """Same word, different place, different answer to the question."""
    rangliste = [rel for rel, _ in index.search("Schreibstil")]
    assert rangliste[0] == "Schreibstil.md"


def test_a_prefix_is_enough(index: WordIndex) -> None:
    """Results narrow while somebody types, not at the last letter."""
    assert {rel for rel, _ in index.search("schreib")} >= {"Schreibstil.md"}


def test_a_typo_within_a_fifth_of_the_word(index: WordIndex) -> None:
    assert {rel for rel, _ in index.search("Schreibstl")} >= {"Schreibstil.md"}


def test_a_short_word_has_no_room_for_a_typo(index: WordIndex) -> None:
    """A fifth of three letters is none, and it has to stay that way: otherwise
    every three-letter word finds every other one."""
    assert not index.search("xyz")


def test_several_words_are_combined_with_or(index: WordIndex) -> None:
    treffer = {rel for rel, _ in index.search("Schreibstil davon")}
    assert treffer == {"Schreibstil.md", "Anderes.md", "Drittes.md"}


def test_an_empty_query_finds_nothing(index: WordIndex) -> None:
    assert index.search("") == []
    assert index.search("   ") == []
