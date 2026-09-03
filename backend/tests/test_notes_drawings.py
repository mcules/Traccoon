"""The drawing inside a file, and putting it back without touching the rest."""
from __future__ import annotations

import json

import lzstring

from app.notes import drawings as d

SCENE = {"type": "excalidraw", "version": 2, "elements": [{"id": "a", "x": 1.5}],
         "appState": {"viewBackgroundColor": "#fff"}, "files": {}}


def packed(scene: dict) -> str:
    return lzstring.LZString().compressToBase64(json.dumps(scene, separators=(",", ":")))


NOTE = (
    "---\ntags: [zeichnung]\n---\n\n"
    "Was ich mir dazu notiert habe.\n\n"
    "# Text Elements\nEin Kasten\n\n"
    "## Drawing\n```compressed-json\n" + packed(SCENE) + "\n```\n%%\n"
)


def test_a_plain_file_is_the_scene_and_nothing_else() -> None:
    assert d.read(json.dumps(SCENE))["elements"] == SCENE["elements"]


def test_the_scene_comes_out_of_the_compressed_block() -> None:
    assert d.read(NOTE)["elements"] == SCENE["elements"]


def test_a_plain_block_is_read_too() -> None:
    note = "text\n\n```json\n" + json.dumps(SCENE) + "\n```\n"
    assert d.read(note)["elements"] == SCENE["elements"]


def test_line_breaks_in_the_compressed_form_are_not_data() -> None:
    """It is broken across lines to keep the file readable-ish."""
    raw = packed(SCENE)
    broken = "\n".join(raw[i:i + 40] for i in range(0, len(raw), 40))
    assert d.read(f"x\n\n```compressed-json\n{broken}\n```\n")["elements"] == SCENE["elements"]


def test_what_cannot_be_read_is_an_empty_canvas() -> None:
    """Not an error: what is on screen then is something to draw on, and what is
    on disk is untouched until somebody saves."""
    assert d.read("{kaputt")["elements"] == []
    assert d.read("nur Text ohne Block")["elements"] == []
    assert d.read("```compressed-json\nkein gültiges Paket\n```")["elements"] == []


def test_the_open_drawing_map_is_not_handed_back() -> None:
    """`collaborators` is a map while a drawing is open and a leftover in the
    file; giving it back makes the editor complain about a shape it cannot use."""
    scene = dict(SCENE, appState={"collaborators": {"a": 1}, "zoom": 2})
    assert d.read(json.dumps(scene))["appState"] == {"zoom": 2}


def test_everything_around_the_block_survives_a_save() -> None:
    changed = dict(SCENE, elements=[{"id": "b", "x": 9}])
    out = d.write(NOTE, changed)
    assert d.read(out)["elements"] == changed["elements"]
    assert d.BLOCK.sub("<>", out) == d.BLOCK.sub("<>", NOTE)
    assert "Was ich mir dazu notiert habe." in out
    assert "tags: [zeichnung]" in out


def test_a_file_that_was_compressed_stays_compressed() -> None:
    """So that opening it elsewhere afterwards shows no difference other than
    the drawing itself."""
    assert "```compressed-json" in d.write(NOTE, SCENE)
    plain = "x\n\n```json\n" + json.dumps(SCENE) + "\n```\n"
    out = d.write(plain, SCENE)
    assert "```json" in out and "compressed" not in out


def test_a_note_with_no_block_yet_keeps_its_text() -> None:
    out = d.write("Meine Notiz\n", SCENE)
    assert out.startswith("Meine Notiz")
    assert d.read(out)["elements"] == SCENE["elements"]


def test_a_plain_file_stays_readable_for_people() -> None:
    out = d.write(json.dumps(SCENE), SCENE)
    assert out.startswith("{\n  ")
    assert d.read(out)["elements"] == SCENE["elements"]


def test_who_made_the_drawing_is_kept() -> None:
    """The file says which program wrote it; a save here must not claim it."""
    scene = dict(SCENE, source="https://example.invalid/plugin/1.2.3")
    out = d.read(d.write(json.dumps(scene), d.read(json.dumps(scene))))
    assert out["source"] == "https://example.invalid/plugin/1.2.3"


def test_the_codec_is_the_one_the_format_uses() -> None:
    """What is in the vault was written by something else and has to stay
    readable by it. This pins the compressed form against a string that
    implementation produced, so a changed library is caught here."""
    from_js = "N4IgLgngDgpiBcID2AnA1gSwOZoQMwEMAbAgYwBcRDkAaEAV2WQCMBnUqAOwFsSAmABlYAWVgFYAdBIC+AXQKgAxAlBQAKmABMYNMlR4gA"
    assert lzstring.LZString().decompressFromBase64(from_js) is not None


def test_only_a_drawing_is_a_drawing() -> None:
    assert d.is_drawing("a/b.excalidraw")
    assert d.is_drawing("a/b.excalidraw.md")
    assert d.is_drawing("A/B.EXCALIDRAW.MD")
    assert not d.is_drawing("a/b.md")
    assert not d.is_drawing("a/excalidraw.png")
