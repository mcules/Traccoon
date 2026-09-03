"""A note taken apart: its properties, its tags, its tasks, its links."""
from __future__ import annotations

from app.notes.dv import settings as plugin_settings
from app.notes.dv.pages import PageIndex, parse_page

STAT = {"size": 10, "ctimeMs": 1_700_000_000_000, "mtimeMs": 1_700_000_000_000}


def page(text: str, rel: str = "a.md"):
    return parse_page(rel, text, STAT)


def test_a_property_written_in_the_block_stays_the_text_it_is() -> None:
    """This is what keeps a telephone number from becoming a number and losing
    its leading zero. A field written inline is read with the full grammar,
    where a number is a number — the two places are deliberately different."""
    p = page("---\ntelefon: '0602183139'\n---\n\nzahl:: 42\n")
    assert p.fields["telefon"] == "0602183139"
    assert p.fields["zahl"] == 42


def test_a_date_in_the_block_is_read_as_a_moment_in_utc() -> None:
    """The reader on the other side hands back midnight UTC for a bare date.
    Reading it as local would move it by the offset of the zone."""
    p = page("---\ndate: 2026-09-02\n---\n")
    assert p.fields["date"]["ts"] == 1_788_307_200_000     # 2026-09-02T00:00Z


def test_tags_come_from_the_text_and_from_the_block() -> None:
    p = page("---\ntags: [aus/dem, block]\n---\n\n#im-text und #noch/einer\n")
    assert p.tags == ["#im-text", "#noch/einer", "#aus/dem", "#block"]
    assert p.etags == ["#im-text", "#noch/einer"]          # only the text ones


def test_a_hash_inside_a_word_is_not_a_tag() -> None:
    p = page("Wir nutzen C# und die Farbe #fff steht in http://x/#anker\n")
    assert p.tags == ["#fff"]


def test_a_fenced_block_holds_no_tags_and_no_links() -> None:
    """A ```dataviewjs block is full of `#` and `[[` that are code. Blanking it
    rather than dropping it keeps every later line number where it was."""
    p = page("```js\n#nicht-ein-tag\n[[Kein Link]]\n```\n\n#doch\n")
    assert p.tags == ["#doch"]
    assert p.outlinks == []


def test_a_task_carries_its_dates_its_tags_and_its_section() -> None:
    plugin_settings.reset()
    p = page("## Offen\n\n- [ ] Etwas tun 📅 2026-09-02 #wichtig\n")
    t = p.tasks[0]
    assert t["section"] == "Offen"
    assert t["tags"] == ["#wichtig"]
    assert t["due"]["hasTime"] is False
    assert t["completed"] is False
    assert "done" not in t                    # absent, not null


def test_a_subtask_hangs_under_its_parent() -> None:
    p = page("- [ ] oben\n  - [x] unten\n")
    assert len(p.tasks) == 1
    assert [c["text"] for c in p.tasks[0]["children"]] == ["unten"]


def test_a_task_counts_as_finished_only_with_its_children() -> None:
    done = page("- [x] oben\n  - [x] unten\n").tasks[0]
    half = page("- [x] oben\n  - [ ] unten\n").tasks[0]
    assert done["fullyCompleted"] is True
    assert half["completed"] is True and half["fullyCompleted"] is False


def test_a_status_that_is_configured_says_what_it_means() -> None:
    plugin_settings.reset()
    assert page("- [/] laeuft\n").tasks[0]["statusType"] == "DONE"
    plugin_settings._statuses = plugin_settings.CORE_STATUSES + [
        {"symbol": "/", "name": "In Progress", "nextStatusSymbol": "x",
         "type": "IN_PROGRESS"}]
    assert page("- [/] laeuft\n").tasks[0]["statusType"] == "IN_PROGRESS"
    plugin_settings.reset()


def test_line_numbers_count_the_block_at_the_top() -> None:
    """A task is written back by its line number, so the block has to count."""
    p = page("---\na: 1\n---\n\n- [ ] etwas\n")
    assert p.tasks[0]["line"] == 4


def test_carriage_returns_do_not_swallow_the_tasks() -> None:
    """A pattern ending in `(.*)$` never matches a line that still carries one,
    which silently dropped every task in a file written on Windows."""
    assert len(page("- [ ] eins\r\n- [ ] zwei\r\n").tasks) == 2


def test_the_day_of_a_note_comes_from_the_name_when_nothing_says_otherwise() -> None:
    assert page("", "05 Daily/2026-09-02.md").day is not None
    assert page("---\nday: 2026-01-01\n---\n", "05 Daily/2026-09-02.md") \
        .day["ts"] == 1_767_225_600_000       # the field wins over the name


def test_a_link_written_in_a_property_counts_as_a_link() -> None:
    index = PageIndex()
    index.build([
        ("Person.md", "---\nfirma: '[[Firma]]'\n---\n", STAT),
        ("Firma.md", "# Firma\n", STAT),
    ])
    assert [l["path"] for l in index.inlinks_of("Firma.md")] == ["Person.md"]


def test_a_link_finds_its_note_wherever_it_lies() -> None:
    index = PageIndex()
    index.build([("Ordner/Ziel.md", "", STAT), ("Quelle.md", "[[Ziel]]", STAT)])
    assert index.resolve("Ziel") == "Ordner/Ziel.md"
    assert index.resolve("Ordner/Ziel") == "Ordner/Ziel.md"
    assert index.resolve("Fehlt") is None


def test_removing_a_note_does_not_unresolve_its_namesake() -> None:
    """Two notes can share a bare name. Dropping the name blindly on a delete
    would leave the other one unreachable by that name."""
    index = PageIndex()
    index.build([("A/Ziel.md", "", STAT), ("B/Ziel.md", "", STAT)])
    kept = index.resolve("Ziel")
    other = "B/Ziel.md" if kept == "A/Ziel.md" else "A/Ziel.md"
    index.remove(other)
    assert index.resolve("Ziel") == kept
