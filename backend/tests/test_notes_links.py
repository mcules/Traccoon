"""What a note is made of, and who points at whom."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.notes.index.links import LinkGraph
from app.notes.model.note import link_key, parse
from app.notes.vault.files import Vault


def test_links_lose_label_and_heading() -> None:
    n = parse("a.md", "See [[Target|the label]] and [[Other#Section]] and ![[Bild.png]].")
    assert n.links == ["Target", "Other", "Bild.png"]


def test_a_link_is_named_once_however_often_it_appears() -> None:
    n = parse("a.md", "[[X]] and [[X]] and [[x]]")
    assert n.links == ["X", "x"]        # case is kept, the graph folds it later
    assert link_key("X") == link_key("x") == "x"


def test_link_key_drops_the_extension() -> None:
    assert link_key("Note.md") == "note"
    assert link_key("Note.markdown") == "note"
    assert link_key("Bild.png") == "bild.png"   # not a note, so not stripped


def test_tags_from_the_text_and_from_the_block() -> None:
    n = parse("a.md", "---\ntags: [aus/dem, block]\n---\n\n#im-text und #noch/einer\n")
    assert n.tags == ["im-text", "noch/einer", "aus/dem", "block"]


def test_a_hash_in_the_middle_of_a_word_is_not_a_tag() -> None:
    """Otherwise every `C#`, every colour and every anchor becomes one."""
    n = parse("a.md", "Wir nutzen C# und die Farbe #fff steht in http://x/#anker")
    assert n.tags == ["fff"]            # after a space, so it counts; C# does not


def test_tags_as_one_string_in_the_block() -> None:
    n = parse("a.md", "---\ntags: eins, zwei drei\n---\n")
    assert n.tags == ["eins", "zwei", "drei"]


def test_the_title_comes_from_the_block_or_from_the_name() -> None:
    assert parse("Folder/Die Notiz.md", "text").title == "Die Notiz"
    assert parse("a.md", "---\ntitle: Anders\n---\n").title == "Anders"


def test_headings_without_their_hashes() -> None:
    n = parse("a.md", "# Eins\n\ntext\n\n### Drei\n")
    assert n.headings == ["Eins", "Drei"]


def test_frontmatter_is_not_part_of_the_body() -> None:
    """A `#tag` inside the block must not be read twice, and a `---` fence is
    not a heading."""
    n = parse("a.md", "---\ntags: [a]\n---\n\n# Kopf\n")
    assert n.tags == ["a"]
    assert n.headings == ["Kopf"]


@pytest.fixture()
def graph(tmp_path: Path) -> tuple[LinkGraph, Vault]:
    root = tmp_path / "v"
    (root / "Personen").mkdir(parents=True)
    (root / "A.md").write_text("Siehe [[B]] und [[Personen/C]]\n", encoding="utf-8")
    (root / "B.md").write_text("Zurück zu [[A]] #projekt\n", encoding="utf-8")
    (root / "Personen" / "C.md").write_text("nichts #projekt #privat\n", encoding="utf-8")
    v = Vault(root)
    g = LinkGraph()
    g.build(v)
    return g, v


def test_a_link_finds_the_note_wherever_it_lies(graph) -> None:
    g, _ = graph
    assert g.resolve("B") == "B.md"
    assert g.resolve("C") == "Personen/C.md"
    assert g.resolve("Personen/C") == "Personen/C.md"
    assert g.resolve("gibtesnicht") is None


def test_backlinks_by_bare_name_and_by_path(graph) -> None:
    g, _ = graph
    assert g.backlinks("B.md") == ["A.md"]
    assert g.backlinks("Personen/C.md") == ["A.md"]
    assert g.backlinks("A.md") == ["B.md"]


def test_a_note_does_not_link_to_itself(tmp_path: Path) -> None:
    root = tmp_path / "v"
    root.mkdir()
    (root / "Selbst.md").write_text("[[Selbst]]\n", encoding="utf-8")
    g = LinkGraph()
    g.build(Vault(root))
    assert g.backlinks("Selbst.md") == []


def test_tags_counted_across_the_vault(graph) -> None:
    g, _ = graph
    assert g.all_tags() == [{"tag": "projekt", "count": 2}, {"tag": "privat", "count": 1}]


def test_one_changed_file_does_not_need_the_whole_vault(graph) -> None:
    g, v = graph
    (Path(v.root) / "B.md").write_text("keine Verweise mehr\n", encoding="utf-8")
    g.update(v, "B.md")
    assert g.backlinks("A.md") == []
    assert g.resolve("B") == "B.md"


def test_a_removed_note_leaves_the_graph(graph) -> None:
    g, v = graph
    g.update(v, "B.md", removed=True)
    assert g.resolve("B") is None
    assert "B.md" not in g.outgoing


def test_removing_one_of_two_notes_of_the_same_name_keeps_the_other(tmp_path: Path) -> None:
    """Two notes can share a bare name. Dropping the name blindly on a delete
    would unresolve the one that is still there."""
    root = tmp_path / "v"
    (root / "eins").mkdir(parents=True)
    (root / "zwei").mkdir()
    (root / "eins" / "Doppelt.md").write_text("a", encoding="utf-8")
    (root / "zwei" / "Doppelt.md").write_text("b", encoding="utf-8")
    v = Vault(root)
    g = LinkGraph()
    g.build(v)
    bleibt = g.resolve("Doppelt")
    anderer = "eins/Doppelt.md" if bleibt == "zwei/Doppelt.md" else "zwei/Doppelt.md"
    g.update(v, anderer, removed=True)
    assert g.resolve("Doppelt") == bleibt
