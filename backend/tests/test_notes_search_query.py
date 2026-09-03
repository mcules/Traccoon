"""The search language: what a query means before anything is searched."""
from __future__ import annotations

from app.notes.query.search import (And, Cmp, Const, Not, Op, Or, Phrase, Prop,
                                     Regex, Text, parse_query, plain_terms, tokenize)


def test_plain_words_are_an_and() -> None:
    q = parse_query("eins zwei")
    assert isinstance(q, And)
    assert [i.value for i in q.items] == ["eins", "zwei"]


def test_space_binds_tighter_than_or() -> None:
    """`a b OR c` is (a and b) or c, not a and (b or c)."""
    q = parse_query("a b OR c")
    assert isinstance(q, Or)
    assert isinstance(q.items[0], And)
    assert isinstance(q.items[1], Text)


def test_an_operator_takes_what_follows_it() -> None:
    q = parse_query("tag:idee")
    assert isinstance(q, Op) and q.op == "tag"
    assert isinstance(q.item, Text) and q.item.value == "idee"


def test_an_operator_without_a_term_still_asks() -> None:
    q = parse_query("task:")
    assert isinstance(q, Op) and q.op == "task" and q.item.value == ""


def test_a_word_that_looks_like_an_operator_is_only_one_before_a_colon() -> None:
    assert isinstance(parse_query("path"), Text)
    assert isinstance(parse_query("path:x"), Op)


def test_a_hyphen_inside_a_word_is_not_a_negation() -> None:
    """This is the one that decides between `task-done:` and `a -b`."""
    q = parse_query("task-done:x")
    assert isinstance(q, Op) and q.op == "task-done"
    q = parse_query("a -b")
    assert isinstance(q, And) and isinstance(q.items[1], Not)


def test_a_minus_after_a_bracket_or_colon_negates() -> None:
    q = parse_query("(-a)")
    assert isinstance(q, Not)


def test_quotes_hold_a_phrase_together() -> None:
    q = parse_query('"zwei Wörter"')
    assert isinstance(q, Phrase) and q.value == "zwei Wörter"


def test_an_unclosed_quote_takes_the_rest() -> None:
    q = parse_query('"ohne Ende')
    assert isinstance(q, Phrase) and q.value == "ohne Ende"


def test_slashes_hold_a_regular_expression() -> None:
    q = parse_query("/ab[0-9]+/")
    assert isinstance(q, Regex) and q.source == "ab[0-9]+"


def test_a_path_like_word_keeps_its_slashes() -> None:
    """`03 Bereiche/Personen` is a path, not the start of a regular expression."""
    q = parse_query("path:03/Personen")
    assert isinstance(q, Op) and isinstance(q.item, Text)
    assert q.item.value == "03/Personen"


def test_brackets_ask_about_a_property() -> None:
    q = parse_query("[status]")
    assert isinstance(q, Prop) and q.name.value == "status" and q.value is None
    q = parse_query("[status:aktiv]")
    assert isinstance(q, Prop) and q.value.value == "aktiv"


def test_the_three_constants() -> None:
    for word, meaning in (("TRUE", "true"), ("FALSE", "false"), ("EMPTY", "empty")):
        q = parse_query(word)
        assert isinstance(q, Const) and q.value == meaning


def test_comparisons() -> None:
    q = parse_query("[size:>100]")
    assert isinstance(q, Prop) and isinstance(q.value, Cmp) and q.value.dir == ">"


def test_groups_change_the_binding() -> None:
    q = parse_query("(a OR b) c")
    assert isinstance(q, And)
    assert isinstance(q.items[0], Or)


def test_an_empty_query_is_nothing() -> None:
    assert parse_query("") is None
    assert parse_query("   ") is None


def test_plain_terms_only_from_operators_that_read_text() -> None:
    assert plain_terms(parse_query("hallo content:welt tag:idee")) == ["hallo", "welt"]


def test_the_tokenizer_keeps_the_case_of_words() -> None:
    assert [t.value for t in tokenize("Grosses OR kleines")] == ["Grosses", "OR", "kleines"]
