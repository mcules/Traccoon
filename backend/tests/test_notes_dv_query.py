"""The query language: read, and run against a small vault."""
from __future__ import annotations

import pytest

from app.notes.dv import settings as plugin_settings
from app.notes.dv.dql import execute, parse_query, evaluate_inline
from app.notes.dv.pages import PageIndex

STAT = {"size": 10, "ctimeMs": 1_700_000_000_000, "mtimeMs": 1_700_000_000_000}

NOTES = {
    "02 Projekte/Alpha.md":
        "---\nstatus: aktiv\ntags: [projekt]\nzahl: 3\n---\n\n"
        "# Alpha\n\n[[03 Firmen/Acme]]\n\n- [ ] offen 📅 2026-09-02 #wichtig\n"
        "- [x] erledigt\n",
    "02 Projekte/Beta.md":
        "---\nstatus: pausiert\ntags: [projekt]\nzahl: 10\n---\n\n"
        "# Beta\n\n- [ ] auch offen\n",
    "03 Firmen/Acme.md": "---\ntags: [firma]\n---\n\n# Acme\n",
    "03 Firmen/9 Leben.md": "---\ntags: [firma]\n---\n\n# 9 Leben\n",
}


@pytest.fixture()
def index() -> PageIndex:
    plugin_settings.reset()
    idx = PageIndex()
    idx.build([(rel, text, STAT) for rel, text in NOTES.items()])
    return idx


def paths(result: dict) -> list[str]:
    return [item["path"] for item in result["items"]]


# ------------------------------------------------------------------ reading

def test_a_query_without_a_head_is_a_list() -> None:
    assert parse_query('FROM "02 Projekte"').type == "LIST"


def test_a_column_without_a_name_is_labelled_by_its_expression() -> None:
    q = parse_query("TABLE file.mtime, status AS Stand")
    assert [c["name"] for c in q.cols] == ["file.mtime", "Stand"]


def test_a_minus_inside_a_word_is_not_a_negation() -> None:
    q = parse_query('LIST WHERE contains(file.name, "task-done")')
    assert q.steps[0].e.args[1].v == "task-done"


def test_a_line_comment_does_not_stop_a_query_parsing() -> None:
    q = parse_query('// what this does\nLIST FROM "02 Projekte"\n')
    assert q.source.path == "02 Projekte"


def test_a_query_that_cannot_be_read_is_an_answer_and_not_a_crash(index) -> None:
    out = execute(index, "LIST WHERE [[unclosed")
    assert out["kind"] == "error"


# ------------------------------------------------------------------ running

def test_from_a_folder_takes_that_folder_and_below(index) -> None:
    assert paths(execute(index, 'LIST FROM "02 Projekte"')) == [
        "02 Projekte/Alpha.md", "02 Projekte/Beta.md"]


def test_from_a_tag_takes_everything_under_it(index) -> None:
    assert len(execute(index, "LIST FROM #firma")["items"]) == 2


def test_where_filters_and_sort_orders(index) -> None:
    out = execute(index, 'LIST FROM "02 Projekte" WHERE zahl > 5')
    assert paths(out) == ["02 Projekte/Beta.md"]


def test_sorting_reads_numbers_in_a_name_as_numbers(index) -> None:
    """`9 Leben` before `Acme`: digits sort before letters, and two runs of
    digits by value. Comparing code points would answer differently."""
    out = execute(index, 'LIST FROM "03 Firmen" SORT file.name ASC')
    assert paths(out) == ["03 Firmen/9 Leben.md", "03 Firmen/Acme.md"]


def test_a_table_puts_the_note_in_front_unless_told_not_to(index) -> None:
    with_id = execute(index, 'TABLE status FROM "02 Projekte"')
    without = execute(index, 'TABLE WITHOUT ID status FROM "02 Projekte"')
    assert with_id["headers"] == ["File", "status"]
    assert without["headers"] == ["status"]
    assert len(without["rows"][0]) == 1


def test_a_task_query_reaches_the_subtasks_too(index) -> None:
    out = execute(index, "TASK WHERE !completed")
    assert len(out["groups"][0]["tasks"]) == 2


def test_this_means_the_note_the_block_sits_in(index) -> None:
    out = execute(index, "LIST WHERE file.link = this.file.link",
                  "02 Projekte/Alpha.md")
    assert paths(out) == ["02 Projekte/Alpha.md"]


def test_links_can_be_followed_in_both_directions(index) -> None:
    incoming = execute(index, "LIST FROM [[03 Firmen/Acme]]")
    outgoing = execute(index, "LIST FROM outgoing([[02 Projekte/Alpha]])")
    assert paths(incoming) == ["02 Projekte/Alpha.md"]
    assert paths(outgoing) == ["03 Firmen/Acme.md"]


def test_a_source_takes_a_written_link_and_not_an_expression(index) -> None:
    """`outgoing(this.file.link)` reads as no target at all and answers with
    nothing. Copied rather than fixed: a query that answers differently here
    than in the note it was written for is worse than one that answers oddly in
    both, and somebody would look for the cause in their notes."""
    out = execute(index, "LIST FROM outgoing(this.file.link)",
                  "02 Projekte/Alpha.md")
    assert out["items"] == []


def test_flatten_makes_one_row_per_item(index) -> None:
    out = execute(index, 'TABLE WITHOUT ID T.text FROM "02 Projekte" '
                         "FLATTEN file.tasks AS T WHERE !T.completed")
    assert [r[0] for r in out["rows"]] == ["offen 📅 2026-09-02 #wichtig",
                                           "auch offen"]


def test_group_by_collects_the_rows(index) -> None:
    out = execute(index, 'LIST FROM "02 Projekte" GROUP BY status')
    assert sorted(g["key"] for g in out["groups"]) == ["aktiv", "pausiert"]


def test_clauses_run_in_the_order_they_were_written(index) -> None:
    """A WHERE after a FLATTEN sees the flattened rows. That is the difference
    between "notes with an open task" and "open tasks"."""
    out = execute(index, 'LIST FROM "02 Projekte" FLATTEN file.tasks AS T '
                         "WHERE T.completed")
    assert len(out["items"]) == 1


# ---------------------------------------------------------------- functions

def test_a_pattern_the_other_engine_refuses_does_nothing_here_either(index) -> None:
    """`[^]]*]` is what the escapes of a string literal leave behind. That
    engine refuses it, the call catches the refusal and the text comes back
    unchanged — so it must not quietly start working here."""
    out = execute(index, 'TABLE WITHOUT ID regexreplace(file.name, "[^]]*]", "") '
                         'FROM "03 Firmen" SORT file.name ASC')
    assert [r[0] for r in out["rows"]] == ["9 Leben", "Acme"]


def test_an_unknown_function_is_an_error_and_not_a_null(index) -> None:
    out = execute(index, "LIST WHERE gibtsnicht(1)")
    assert out["kind"] == "error"


def test_the_function_library_answers_the_usual_questions(index) -> None:
    out = execute(index, 'TABLE WITHOUT ID '
                         'upper(file.name), length(file.tasks), '
                         'default(nichts, "—"), round(1.005, 2) '
                         'FROM "02 Projekte" SORT file.name ASC')
    assert out["rows"][0] == ["ALPHA", 2, "—", 1.0]


def test_an_inline_expression_reads_the_note_around_it(index) -> None:
    assert evaluate_inline(index, "this.status", "02 Projekte/Alpha.md") == \
        {"ok": True, "value": "aktiv"}
    assert evaluate_inline(index, "status", "02 Projekte/Alpha.md")["value"] == "aktiv"
