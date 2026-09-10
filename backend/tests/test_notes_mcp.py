"""The notes as tools.

What matters here is what a model gets back. It cannot read a stack trace and
it cannot act on an errno, so every way a call can go wrong has to come back as
a sentence saying what to do instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import scopes
from app.notes import registry
from app.services import notes_mcp

NOTES = {
    "02 Projekte/Alpha.md":
        "---\nstatus: aktiv\ntags: [projekt]\n---\n\n# Alpha\n\n"
        "Siehe [[Ziel]].\n\n## Offen\n\n- [ ] etwas tun\n",
    "Ordner/Ziel.md": "# Ziel\n\nZwiebelkuchen.\n",
}


@pytest.fixture()
def user(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    for rel, text in NOTES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    registry.forget_all()
    monkeypatch.setattr(registry, "_settings_loaded", True)

    class U:
        vault_path = str(root)
    return U()


async def call(user, tool, **args):
    """`name` is a tool argument of its own, so the tool itself is not called that."""
    return await notes_mcp.execute(user, tool, args)


# ------------------------------------------------------------------ the list

def test_every_tool_says_what_it_needs() -> None:
    for tool in notes_mcp.toollist():
        schema = tool["inputSchema"]
        assert tool["description"].strip()
        for needed in schema["required"]:
            assert needed in schema["properties"], (tool["name"], needed)


def test_the_scope_reaches_the_tool_server_and_nothing_else() -> None:
    """The token goes into the configuration of a foreign program, so what it
    opens has to be one thing that can be named."""
    only = {scopes.NOTES}
    assert scopes.allowed(only, "POST", "/mcp/notes")
    assert not scopes.allowed(only, "GET", "/notes-native/files/")
    assert not scopes.allowed(only, "GET", "/projects")
    assert not scopes.allowed(only, "POST", "/mcp/mail")


# ------------------------------------------------------------------ reading

@pytest.mark.asyncio
async def test_reading_gives_the_text_the_properties_and_the_version(user) -> None:
    out = await call(user, "notes_read", path="02 Projekte/Alpha.md")
    assert out["properties"]["status"] == "aktiv"
    assert out["tags"] == ["#projekt"]
    assert out["truncated"] is False
    assert out["hash"]


@pytest.mark.asyncio
async def test_a_note_that_is_not_there_reads_as_a_sentence(user) -> None:
    with pytest.raises(LookupError) as err:
        await call(user, "notes_read", path="gibtsnicht.md")
    assert "no note" in str(err.value)


@pytest.mark.asyncio
async def test_listing_can_be_narrowed_to_a_folder(user) -> None:
    out = await call(user, "notes_list", folder="Ordner")
    assert [n["path"] for n in out["notes"]] == ["Ordner/Ziel.md"]


@pytest.mark.asyncio
async def test_searching_and_the_two_note_languages_answer(user) -> None:
    assert (await call(user, "notes_search", query="Zwiebelkuchen"))["total"] == 1
    listed = await call(user, "notes_query", query='LIST FROM "02 Projekte"')
    assert listed["kind"] == "list" and len(listed["items"]) == 1
    tasks = await call(user, "notes_tasks", query="not done")
    assert tasks["total"] == 1


# ------------------------------------------------------------------ writing

@pytest.mark.asyncio
async def test_writing_lands_and_is_findable_at_once(user) -> None:
    """The tools sit in the same process as the index, so what was just written
    is searchable before the call returns."""
    await call(user, "notes_write", path="Neu.md", content="# Neu\n\nHolunder.\n")
    assert (await call(user, "notes_search", query="Holunder"))["total"] == 1


@pytest.mark.asyncio
async def test_writing_over_a_change_is_refused_with_the_text_that_is_there(user) -> None:
    stale = (await call(user, "notes_read", path="Ordner/Ziel.md"))["hash"]
    await call(user, "notes_write", path="Ordner/Ziel.md", content="von woanders\n")
    with pytest.raises(ValueError) as err:
        await call(user, "notes_write", path="Ordner/Ziel.md",
                   content="meins\n", base_hash=stale)
    assert "von woanders" in str(err.value)


@pytest.mark.asyncio
async def test_appending_does_not_need_the_rest_of_the_note(user) -> None:
    await call(user, "notes_append", path="Ordner/Ziel.md", text="Noch ein Satz.")
    text = (await call(user, "notes_read", path="Ordner/Ziel.md"))["content"]
    assert text.endswith("Noch ein Satz.\n")
    assert "Zwiebelkuchen" in text


@pytest.mark.asyncio
async def test_appending_under_a_heading_lands_at_the_end_of_its_section(user) -> None:
    """At the end of the section, not right after the heading: an entry added to
    a list belongs after the entries already in it."""
    await call(user, "notes_append", path="02 Projekte/Alpha.md",
               text="- [ ] noch etwas", heading="Offen")
    text = (await call(user, "notes_read", path="02 Projekte/Alpha.md"))["content"]
    assert text.index("etwas tun") < text.index("noch etwas")


@pytest.mark.asyncio
async def test_appending_to_a_note_that_is_not_there_creates_it(user) -> None:
    await call(user, "notes_append", path="Frisch.md", text="erste Zeile")
    assert (await call(user, "notes_read", path="Frisch.md"))["content"] == "erste Zeile\n"


@pytest.mark.asyncio
async def test_a_replacement_that_does_not_match_writes_nothing(user) -> None:
    with pytest.raises(ValueError):
        await call(user, "notes_replace", path="Ordner/Ziel.md",
                   find="Sauerkraut", replace="x")
    with pytest.raises(ValueError) as err:
        await call(user, "notes_replace", path="Ordner/Ziel.md",
                   find="Zwiebelkuchen", replace="x", expect=2)
    assert "nothing was written" in str(err.value)
    assert "Zwiebelkuchen" in (await call(user, "notes_read",
                                          path="Ordner/Ziel.md"))["content"]


@pytest.mark.asyncio
async def test_properties_are_changed_one_key_at_a_time(user) -> None:
    """Writing the whole block would throw away what the call did not name."""
    out = await call(user, "notes_properties", path="02 Projekte/Alpha.md",
                     set={"status": "pausiert", "neu": 1})
    assert out["properties"]["status"] == "pausiert"
    assert out["properties"]["neu"] == 1
    assert out["properties"]["tags"] == ["projekt"]      # untouched
    text = (await call(user, "notes_read", path="02 Projekte/Alpha.md"))["content"]
    assert text.startswith("---\n") and "# Alpha" in text


@pytest.mark.asyncio
async def test_tags_can_be_added_and_taken_away(user) -> None:
    out = await call(user, "notes_tags", path="02 Projekte/Alpha.md",
                     add=["#wichtig"], remove=["projekt"])
    assert out["tags"] == ["wichtig"]
    assert (await call(user, "notes_tags"))["tags"]      # the whole vault


@pytest.mark.asyncio
async def test_moving_takes_the_links_with_it(user) -> None:
    out = await call(user, "notes_move", **{"from": "Ordner/Ziel.md",
                                            "to": "Ordner/Neu.md"})
    assert out["linkUpdates"]["links"] == 1
    text = (await call(user, "notes_read", path="02 Projekte/Alpha.md"))["content"]
    assert "[[Neu]]" in text


@pytest.mark.asyncio
async def test_deleting_puts_it_where_a_person_can_get_it_back(user) -> None:
    out = await call(user, "notes_delete", path="Ordner/Ziel.md")
    assert out["trashed"].startswith(".trash/")


@pytest.mark.asyncio
async def test_an_attachment_arrives_as_bytes(user) -> None:
    import base64
    out = await call(user, "notes_attach", name="bild.png",
                     data=base64.b64encode(b"\x89PNG").decode(),
                     note="02 Projekte/Alpha.md")
    assert out["size"] == 4
    with pytest.raises(ValueError):
        await call(user, "notes_attach", name="x.png", data="kein base64!")


@pytest.mark.asyncio
async def test_reading_says_how_long_the_note_is_and_how_much_came_back(user) -> None:
    """Two numbers instead of a flag to read past.

    A run that has the whole note and reads it again with a larger `max_chars` gets
    nothing new and spends a turn on it. `chars == chars_returned` settles that without
    the reader having to notice a `false`.
    """
    whole = await call(user, "notes_read", path="Ordner/Ziel.md")
    assert whole["chars"] == whole["chars_returned"]
    assert whole["truncated"] is False

    await call(user, "notes_write", path="Lang.md", content="z" * 5_000)
    part = await call(user, "notes_read", path="Lang.md", max_chars=1_200)
    assert part["chars_returned"] == 1_200
    assert part["chars"] == 5_000
    assert part["truncated"] is True


@pytest.mark.asyncio
async def test_a_tiny_max_chars_is_a_probe_and_is_read_as_a_whole_read(user) -> None:
    """`max_chars: 1` asks nothing anybody wants.

    Run 2511 read a note with `max_chars: 1`, took `chars` out of the answer and read it
    again properly: two round trips for a number the first full read would have carried. A
    short read saves nothing — the round trip is the cost, not the characters — so the
    floor turns the probe into the read it stood in for.
    """
    await call(user, "notes_write", path="Lang.md", content="z" * 5_000)
    probe = await call(user, "notes_read", path="Lang.md", max_chars=1)
    assert probe["chars_returned"] == 5_000     # the whole note, not one character
    assert probe["chars"] == 5_000
    assert probe["truncated"] is False


@pytest.mark.asyncio
async def test_a_sync_conflict_comes_back_as_the_lines_that_differ(user) -> None:
    """The case this tool was built for: a conflict copy beside its original.

    What comes back must be the difference and not the notes — a run that got both
    notes in full spent five minutes rebuilding the comparison out of queries.
    """
    await call(user, "notes_write", path="05 Daily/2026-09-07.md",
               content="# Tag\n\n- 09:00 — eins\n- 10:00 — zwei\n")
    await call(user, "notes_write",
               path="05 Daily/2026-09-07.sync-conflict-20260907-183530-EVID52R.md",
               content="# Tag\n\n- 09:00 — eins\n- 10:00 — zwei\n- 11:00 — drei\n")

    out = await call(user, "notes_diff", a="05 Daily/2026-09-07.md",
                     b="05 Daily/2026-09-07.sync-conflict-20260907-183530-EVID52R.md")
    assert out["identical"] is False
    assert "+- 11:00 — drei" in out["diff"]
    # Only the changed line and its context, not both notes back again.
    assert "# Tag" not in out["diff"].split("@@")[0]
    assert not out["truncated"]


@pytest.mark.asyncio
async def test_two_notes_that_are_the_same_say_so_instead_of_answering_nothing(user) -> None:
    """An empty answer reads as a failed call, and a model tries a failed call again."""
    await call(user, "notes_write", path="a.md", content="gleich\n")
    await call(user, "notes_write", path="b.md", content="gleich\n")
    out = await call(user, "notes_diff", a="a.md", b="b.md")
    assert out["identical"] is True
    assert out["diff"] == ""


@pytest.mark.asyncio
async def test_diffing_against_a_note_that_is_not_there_says_which_one(user) -> None:
    with pytest.raises(LookupError):
        await call(user, "notes_diff", a="Ordner/Ziel.md", b="Ordner/gibtsnicht.md")


@pytest.mark.asyncio
async def test_a_tool_nobody_offers_says_so(user) -> None:
    with pytest.raises(LookupError):
        await call(user, "notes_erase_everything")


def test_the_note_scope_does_not_open_the_live_channel() -> None:
    """A token made for the tool server has no business listening in on
    somebody's open window."""
    assert not scopes.allowed({scopes.NOTES}, "GET", "/notes-native/ws")
    assert scopes.allowed(None, "GET", "/notes-native/ws")     # a session may


@pytest.mark.asyncio
async def test_a_replace_shows_what_it_wrote_so_nobody_reads_the_note_back(user) -> None:
    """The verify read after every write was half the round trips of a run.

    Run 2511 wrote six times and read the whole note back after every one of them, only to
    see whether the edit had landed. The answer of the write now says so itself: the new
    identity, the new length, and the changed place in its context.
    """
    await call(user, "notes_write", path="Lang.md", content="a" * 2_000 + "ALT" + "b" * 2_000)
    out = await call(user, "notes_replace", path="Lang.md", find="ALT", replace="NEU")

    assert out["replaced"] == 1
    assert out["chars"] == 4_003
    assert out["hash"]
    assert "NEU" in out["excerpt"]
    assert "ALT" not in out["excerpt"]
    # An excerpt, not the note: the whole point is that it is short.
    assert len(out["excerpt"]) < 1_000

    # And the identity is good enough to write again without reading in between.
    await call(user, "notes_write", path="Lang.md", content="egal", base_hash=out["hash"])


@pytest.mark.asyncio
async def test_changing_properties_hands_back_the_identity(user) -> None:
    """Whoever sets a property usually writes straight after. Without the `hash` in the
    answer that means reading the whole note again just to be allowed to."""
    out = await call(user, "notes_properties", path="Ordner/Ziel.md", set={"status": "aktiv"})

    assert out["properties"]["status"] == "aktiv"
    assert out["hash"]
    await call(user, "notes_write", path="Ordner/Ziel.md", content="egal", base_hash=out["hash"])


@pytest.mark.asyncio
async def test_several_changes_to_one_note_go_in_one_call(user) -> None:
    """Thirteen replacements in one run were thirteen round trips.

    In run 2515 the tools answered in 5,3 seconds altogether while the run took 4:36. What
    costs time is the number of turns, so changes to the same note belong in one.
    """
    await call(user, "notes_write", path="Lang.md",
               content="EINS\n" + "x" * 1_000 + "\nZWEI\n" + "y" * 1_000 + "\nDREI\n")
    out = await call(user, "notes_replace", path="Lang.md", edits=[
        {"find": "EINS", "replace": "1", "expect": 1},
        {"find": "ZWEI", "replace": "2"},
        {"find": "DREI", "replace": "3"},
    ])

    assert out["replaced"] == 3
    assert len(out["excerpts"]) == 3
    assert "1" in out["excerpts"][0] and "2" in out["excerpts"][1]
    text = (await call(user, "notes_read", path="Lang.md"))["content"]
    assert text.startswith("1\n") and "\n2\n" in text and text.endswith("3\n")


@pytest.mark.asyncio
async def test_a_batch_that_cannot_find_one_of_its_changes_writes_nothing(user) -> None:
    """Half an applied batch would leave the note in a state nobody asked for and nobody
    can name. The message says WHICH change was the problem."""
    await call(user, "notes_write", path="Lang.md", content="EINS\nZWEI\n")
    with pytest.raises(ValueError) as err:
        await call(user, "notes_replace", path="Lang.md", edits=[
            {"find": "EINS", "replace": "1"},
            {"find": "GIBTSNICHT", "replace": "x"},
        ])
    assert "change 2" in str(err.value)
    assert "nothing was written" in str(err.value)
    assert (await call(user, "notes_read", path="Lang.md"))["content"] == "EINS\nZWEI\n"


@pytest.mark.asyncio
async def test_one_change_still_answers_the_way_it_did(user) -> None:
    """The single form is what most calls are, and its answer must not grow a list."""
    await call(user, "notes_write", path="Lang.md", content="EINS\n")
    out = await call(user, "notes_replace", path="Lang.md", find="EINS", replace="1")
    assert out["replaced"] == 1
    assert "1" in out["excerpt"]
    assert "excerpts" not in out


# --------------------------------------------------------------- the grounded nought

@pytest.mark.asyncio
async def test_finding_nothing_says_which_part_of_the_query_is_to_blame(user) -> None:
    """A bare `total: 0` gets asked again in another spelling.

    Run 2517 searched five times for open checkboxes in the day's notes and correctly got
    none every time. It could not tell "there are no such checkboxes anywhere" from "none in
    that folder" from "I wrote the query wrong", so it tried three more spellings. Counting
    each part on its own settles all three in one answer.
    """
    out = await call(user, "notes_search", query='path:"Ordner" Zwiebelkuchenkonferenz')

    assert out["total"] == 0
    assert out["searched"] == 2                      # the notes of the fixture
    parts = {p["part"]: p["matches"] for p in out["why_nothing"]}
    assert parts['path:"Ordner"'] == 1               # the folder is there
    assert parts["Zwiebelkuchenkonferenz"] == 0      # the word is not


@pytest.mark.asyncio
async def test_two_parts_that_each_match_are_the_interesting_answer(user) -> None:
    """The case that cost run 2517 five turns: both halves are fine, they just never meet
    in the same note. That is a fact about the vault, not a broken query."""
    out = await call(user, "notes_search", query='path:"Ordner" Alpha')

    assert out["total"] == 0
    parts = {p["part"]: p["matches"] for p in out["why_nothing"]}
    assert parts['path:"Ordner"'] == 1
    assert parts["Alpha"] >= 1


@pytest.mark.asyncio
async def test_a_query_of_one_part_has_nothing_to_take_apart(user) -> None:
    """One part cannot be to blame for itself. `searched` still says how far it looked."""
    out = await call(user, "notes_search", query="Zwiebelkuchenkonferenz")

    assert out["total"] == 0
    assert out["searched"] == 2
    assert "why_nothing" not in out


@pytest.mark.asyncio
async def test_a_search_that_finds_something_stays_as_it_was(user) -> None:
    """The nought is the only case that grew. Nothing is counted when nothing is wrong."""
    out = await call(user, "notes_search", query="Zwiebelkuchen")

    assert out["total"] == 1
    assert "searched" not in out
    assert "why_nothing" not in out
