"""The curator tidies the memory up, and has to be careful doing so.

It touches other people's memories. That is why the tests guard above all the emergency
brakes: what happens when the model is overeager, misses the format or the archive jams.
"""
import datetime as dt

import pytest
from app.services.appsettings import get_setting, set_setting
from app.worker import curator
from app.worker.curator import PIN, _parts, due, curate_note
from conftest import make_user

# Long enough for tidying up to be worth it at all (MINDEST_ZEICHEN), and this is what real
# memory notes look like: one sentence per line, not two words.
NOTE = "\n".join(f"- Insight {i}: the person wants this handled that way for good."
                  for i in range(40))
PATH = "KI/Gedaechtnis/Mensch.md"


class FakeMcp:
    """Vault replacement: remembers what was written and what was appended."""

    def __init__(self, content: str = NOTE, typo: str | None = None):
        self.notes = {PATH: content}
        self.appended: dict[str, str] = {}
        self.typo = typo

    async def call(self, tool: str, args: dict):
        path = (args.get("target") or {}).get("path", "")
        if tool == "obsidian__obsidian_get_note":
            return self.notes.get(path, "")
        if tool == "obsidian__obsidian_append_to_note":
            if self.typo == "archiv":
                raise RuntimeError("Archive not writable")
            self.appended[path] = self.appended.get(path, "") + args["content"]
            return "ok"
        if tool == "obsidian__obsidian_write_note":
            self.notes[path] = args["content"]
            return "ok"
        return "ok"


def _aux(monkeypatch, answer):
    async def fake(*a, **kw):
        return answer
    monkeypatch.setattr("app.worker.aux.aux_chat", fake)


async def _run(db, mcp, owner_id=1):
    return await curate_note(db, mcp, owner_id=owner_id, path=PATH, agent=None,
                                 tokens={}, base_urls={})


def test_answer_split():
    b, a = _parts("### KEEP\n- eins\n- zwei\n### ARCHIVE\n- alt")
    assert b == "- eins\n- zwei" and a == "- alt"
    assert _parts("### KEEP\n- eins\n### ARCHIVE\nnone")[1] == ""
    assert _parts("kein Format") is None
    assert _parts("### KEEP\n\n### ARCHIVE\n- everything gone") is None   # nothing kept


async def test_a_short_note_is_left_alone(db, monkeypatch):
    _aux(monkeypatch, "### KEEP\n- eins")
    mcp = FakeMcp("- nur eine Zeile")
    assert await _run(db, mcp) is None
    assert mcp.notes[PATH] == "- nur eine Zeile"


async def test_pruning_shortens_and_archives(db, monkeypatch):
    keep = "\n".join(f"- Erkenntnis {i}: zusammengefasst." for i in range(20))
    _aux(monkeypatch, f"### KEEP\n{keep}\n### ARCHIVE\n- Erkenntnis 39: alt.")
    mcp = FakeMcp()
    report = await _run(db, mcp)
    assert report and "40 → 20" in report
    assert "Erkenntnis 0" in mcp.notes[PATH] and "Erkenntnis 39" not in mcp.notes[PATH]
    # What is sorted out is not gone but lies in the archive beside it.
    archive = mcp.appended["KI/Gedaechtnis/Archiv-Mensch.md"]
    assert "Erkenntnis 39" in archive and "Aussortiert am" in archive


async def test_pinned_material_has_to_survive(db, monkeypatch):
    """If a pin is missing in the result, nothing is written at all: the human nailed this
    line down explicitly."""
    content = NOTE + f"\n- {PIN} Niemals ohne Rückfrage deployen"
    _aux(monkeypatch, "### KEEP\n" + "\n".join(
        f"- Erkenntnis {i}: zusammengefasst." for i in range(20)))
    mcp = FakeMcp(content)
    assert await _run(db, mcp) is None
    assert mcp.notes[PATH] == content


async def test_clear_cutting_is_refused(db, monkeypatch):
    """Two thirds gone is no longer tidying up."""
    _aux(monkeypatch, "### KEEP\n- eins\n### ARCHIVE\n- der ganze Rest")
    mcp = FakeMcp()
    assert await _run(db, mcp) is None
    assert mcp.notes[PATH] == NOTE


async def test_a_format_error_leaves_everything_standing(db, monkeypatch):
    _aux(monkeypatch, "Klar, ich habe aufgeräumt! Hier das Ergebnis: ...")
    mcp = FakeMcp()
    assert await _run(db, mcp) is None
    assert mcp.notes[PATH] == NOTE


async def test_without_aux_nothing_happens(db, monkeypatch):
    _aux(monkeypatch, None)
    mcp = FakeMcp()
    assert await _run(db, mcp) is None
    assert mcp.notes[PATH] == NOTE


async def test_a_stuck_archive_keeps_the_note_unchanged(db, monkeypatch):
    """Archive first, truncate afterwards: if the archive jams, nothing may be truncated,
    because otherwise what was sorted out would be gone without replacement."""
    keep = "\n".join(f"- Erkenntnis {i}: zusammengefasst." for i in range(20))
    _aux(monkeypatch, f"### KEEP\n{keep}\n### ARCHIVE\n- Erkenntnis 39: alt.")
    mcp = FakeMcp(typo="archiv")
    assert await _run(db, mcp) is None
    assert mcp.notes[PATH] == NOTE


async def test_at_most_once_a_day(db, monkeypatch):
    now = dt.datetime.now(tz=dt.timezone.utc)
    assert await due(db, 1, PATH) is True                     # never run yet
    await set_setting(db, f"curator_last:1:{PATH}", now.isoformat())
    assert await due(db, 1, PATH, now=now) is False
    assert await due(db, 1, PATH, now=now + dt.timedelta(hours=25)) is True
    # An unreadable timestamp must not block permanently.
    await set_setting(db, f"curator_last:1:{PATH}", "kaputt")
    assert await due(db, 1, PATH) is True


async def test_a_successful_run_remembers_the_moment(db, monkeypatch):
    keep = "\n".join(f"- Erkenntnis {i}: zusammengefasst." for i in range(20))
    _aux(monkeypatch, f"### KEEP\n{keep}\n### ARCHIVE\nnone")
    await _run(db, FakeMcp(), owner_id=7)
    assert await get_setting(db, f"curator_last:7:{PATH}", "") != ""
    assert await due(db, 7, PATH) is False


async def test_without_an_own_model_no_curation_happens(db, monkeypatch):
    """The curator is diligence work in the background. If it ran on the working model for
    lack of a setting, it would cost money unasked AND write in the vault of the human."""
    from app.worker import __main__ as worker

    ran = {}

    async def fake_curate(*a, **kw):
        ran["ja"] = True
        return []

    monkeypatch.setattr("app.worker.curator.curate", fake_curate)
    await worker._handle_curator({"owner_id": 1, "agent_role": "assistent"})
    assert not ran


async def test_all_four_areas_are_tidied(db, monkeypatch):
    """The narrowest note has to be tidied too, otherwise it grows until it falls out.

    `Projekt-<KEY>-Agent-<rolle>.md` stands last in the prompt, so it is the first block the
    budget drops once it is long. Leaving it out of the round would quietly undo the area.
    """
    from app.worker.curator import curate

    seen: list[str] = []

    async def fake_note(db_, mcp_, *, owner_id, path, agent, tokens, base_urls):
        seen.append(path.rsplit("/", 1)[-1])
        return None

    monkeypatch.setattr("app.worker.curator.curate_note", fake_note)
    root = "KI/Gedaechtnis"
    user = await make_user(db, "vierbereiche")
    user.vault_memory_path = root
    await db.commit()
    await curate(db, FakeMcp(), owner_id=user.id, agent_role="developer", project_key="TRA")
    assert seen == ["Mensch.md", "Agent-developer.md", "Projekt-TRA.md",
                    "Projekt-TRA-Agent-developer.md"]


async def test_a_projectless_run_tidies_only_what_it_has(db, monkeypatch):
    """Without a project there are no project notes — and none may be invented either."""
    from app.worker.curator import curate

    seen: list[str] = []

    async def fake_note(db_, mcp_, *, owner_id, path, agent, tokens, base_urls):
        seen.append(path.rsplit("/", 1)[-1])
        return None

    monkeypatch.setattr("app.worker.curator.curate_note", fake_note)
    root = "KI/Gedaechtnis"
    user = await make_user(db, "projektlos_kurator")
    user.vault_memory_path = root
    await db.commit()
    await curate(db, FakeMcp(), owner_id=user.id, agent_role="assistent")
    assert seen == ["Mensch.md", "Agent-assistent.md"]
