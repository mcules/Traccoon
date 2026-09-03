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
async def test_a_tool_nobody_offers_says_so(user) -> None:
    with pytest.raises(LookupError):
        await call(user, "notes_erase_everything")


def test_the_note_scope_does_not_open_the_live_channel() -> None:
    """A token made for the tool server has no business listening in on
    somebody's open window."""
    assert not scopes.allowed({scopes.NOTES}, "GET", "/notes-native/ws")
    assert scopes.allowed(None, "GET", "/notes-native/ws")     # a session may
