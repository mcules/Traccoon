"""Memory of the agents in the note vault.

The double below answers to the tools of the house's own note server and to
nothing else. That is the point of it: the previous one accepted every name it
was given, so when the foreign server it was imitating went away, these tests
went on passing while every real write failed. `test_only_tools_that_exist_are_called`
in `test_worker_tool_names.py` guards the same seam from the other side.
"""
import json

import pytest
from conftest import auth, make_user

from app.worker.tools_memory import (
    MAX_MEMORY_CHARS, NO_MEMORY, TEACH_TOOL_NAME, call_memory_tool, call_teach_tool, memory_root,
    note_path, read_memory,
)


class FakeMcp:
    """MCP session replacement: records calls and answers out of `notes`.

    It knows exactly the tools of the note server and refuses everything else,
    the way the real one does. A double that answers to any name cannot notice
    that a tool has vanished — which is how a broken memory went unseen for a
    day while every test here stayed green.
    """

    KNOWN = {"vault__notes_read", "vault__notes_write", "vault__notes_append",
             "vault__notes_search"}

    def __init__(self, notes: dict[str, str] | None = None, fail: set[str] | None = None):
        self.notes = dict(notes or {})
        self.fail = fail or set()
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict) -> str:
        text, _ = await self.call_ex(name, args)
        return text

    async def call_ex(self, name: str, args: dict) -> tuple[str, bool]:
        self.calls.append((name, args))
        if name not in self.KNOWN:
            return f"Error: no tool called {name!r}", True
        if name in self.fail:
            return "MCP error -32000: absichtlich fehlgeschlagen", True
        path = args.get("path", "")
        if name == "vault__notes_read":
            if path not in self.notes:
                return "Error: file not found", True
            # The real one answers with the note AND what is known about it.
            return json.dumps({"path": path, "content": self.notes[path],
                               "truncated": False, "hash": "x",
                               "properties": {}, "tags": []}), False
        if name == "vault__notes_write":
            self.notes[path] = args["content"]
            return json.dumps({"ok": True, "path": path, "hash": "x"}), False
        if name == "vault__notes_append":
            # Creates the note when it is not there, exactly like the real one.
            raw = self.notes.get(path, "")
            joined = raw + ("" if raw.endswith("\n") or not raw else "\n")
            self.notes[path] = joined + args["text"].rstrip("\n") + "\n"
            return json.dumps({"ok": True, "path": path, "hash": "x"}), False
        return f"Treffer für {args.get('query')}", False

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


ROOT = "04 Traccoon/Gedächtnis"


def test_paths():
    """Every area has its note; areas that do not fit yield no path."""
    assert note_path(ROOT, "person") == f"{ROOT}/Mensch.md"
    assert note_path(ROOT, "agent", "developer") == f"{ROOT}/Agent-developer.md"
    assert note_path(ROOT, "project", "developer", "TRA") == f"{ROOT}/Projekt-TRA.md"
    assert note_path(ROOT, "project_agent", "developer", "TRA") \
        == f"{ROOT}/Projekt-TRA-Agent-developer.md"
    # Without a role respectively a project the note does not exist: the caller has to take 'mensch'.
    assert note_path(ROOT, "agent") is None
    assert note_path(ROOT, "project", "developer") is None
    # The narrowest area needs BOTH halves; one of them is not enough for a note.
    assert note_path(ROOT, "project_agent", "developer") is None
    assert note_path(ROOT, "project_agent", "", "TRA") is None
    # No folder configured means the function is off.
    assert note_path("", "person") is None
    assert note_path("  ", "person") is None


def test_paths_without_a_path_change():
    """Role and project key must not be able to leave the folder."""
    p = note_path(ROOT, "project", "", "../../etc")
    assert p is not None and ".." not in p and p.count("/") == ROOT.count("/") + 1
    # `project_agent` builds its name from BOTH, so both halves have to be cleaned.
    p = note_path(ROOT, "project_agent", "../../rolle", "../../etc")
    assert p is not None and ".." not in p and p.count("/") == ROOT.count("/") + 1


async def test_recall_collects_from_the_general_to_the_specific():
    """All four notes land in the block, the narrowest one last.

    The order is not cosmetic: the block that stands last is the one the model weighs most
    when two memories disagree, and the narrowest one is the one that is meant.
    """
    mcp = FakeMcp({
        f"{ROOT}/Mensch.md": "- Commit-Betreffe auf Deutsch.",
        f"{ROOT}/Agent-developer.md": "- Immer Tests mitliefern.",
        f"{ROOT}/Projekt-TRA.md": "- Migration in beiden Tracks pflegen.",
        f"{ROOT}/Projekt-TRA-Agent-developer.md": "- Hier gehoert der Alembic-Stempel dazu.",
    })
    text = await read_memory(mcp, ROOT, "developer", "TRA")
    assert text.index("Commit-Betreffe") < text.index("Tests mitliefern") \
        < text.index("Migration in beiden") < text.index("Alembic-Stempel")
    assert "## About your person" in text and "## For this project" in text
    assert "## For your role in this project" in text


async def test_a_missing_note_on_recall_is_not_an_error():
    """Only Mensch.md exists; the rest is simply missing, without an exception."""
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "- Eine Vorgabe."})
    text = await read_memory(mcp, ROOT, "developer", "TRA")
    assert "Eine Vorgabe" in text
    assert "Für deine Rolle" not in text


async def test_recall_without_a_folder_recalls_nothing():
    """No memory configured means not a single MCP call."""
    mcp = FakeMcp()
    assert await read_memory(mcp, "", "developer", "TRA") == ""
    assert mcp.calls == []


async def test_recall_is_capped():
    """A vault that got out of hand does not bury the assignment."""
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "- Zeile\n" * 5000})
    assert len(await read_memory(mcp, ROOT, "", "")) <= MAX_MEMORY_CHARS


async def test_the_role_memory_survives_a_bloated_person_note():
    """The cap shortens the GENERAL block, never the specific one.

    A person note big enough to fill the budget on its own used to push the role note out of
    the prompt completely — the agent then relearned rules that were long since written down.
    """
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "- allgemein\n" * 5000,
                   f"{ROOT}/Agent-assistent.md": "- nur fuer die Rolle\n" * 20})
    block = await read_memory(mcp, ROOT, "assistent", "")
    assert len(block) <= MAX_MEMORY_CHARS
    assert block.count("- nur fuer die Rolle") == 20
    assert "## About your person" in block


async def test_the_narrowest_note_survives_a_bloated_person_note():
    """The budget is handed out from the back, so the project-and-role note may never fall out.

    It is the last block in the prompt and the one that weighs most; losing it to a person
    note that ran wild would quietly undo the whole area.
    """
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "- allgemein\n" * 5000,
                   f"{ROOT}/Projekt-TRA-Agent-developer.md": "- nur hier\n" * 20})
    block = await read_memory(mcp, ROOT, "developer", "TRA")
    assert len(block) <= MAX_MEMORY_CHARS
    assert block.count("- nur hier") == 20


async def test_remembering_in_the_narrowest_area(db):
    """`remember` writes the fourth note, and creates it on the first insight."""
    u = await make_user(db, "engmerker")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "project_agent", "text": "Alembic-Stempel nicht vergessen."},
                                 agent_role="developer", project_key="TRA")
    assert "Noted" in out
    assert "Alembic-Stempel" in mcp.notes[f"{ROOT}/Projekt-TRA-Agent-developer.md"]


async def test_the_narrowest_area_says_which_half_is_missing(db):
    """Two halves can be missing, so the refusal has to name the one that is."""
    u = await make_user(db, "halblos")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "project_agent", "text": "x"}, agent_role="developer")
    assert out.startswith("ERROR") and "project" in out and "role" not in out
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "project_agent", "text": "x"}, project_key="TRA")
    assert out.startswith("ERROR") and "role" in out
    assert mcp.calls == []


async def test_a_note_quoting_an_error_message_still_reads():
    """A memory line ABOUT an error must not look like a failed read.

    'Section target not found' stood in Agent-assistent.md from 2026-08-01 on; the substring
    check declared every read of that note a failure, so the role memory silently vanished
    from the prompt and the curator skipped the note for weeks.
    """
    note = "- Der Heading-Name schlaegt mit \"Section target not found\" fehl."
    mcp = FakeMcp({f"{ROOT}/Agent-assistent.md": note})
    block = await read_memory(mcp, ROOT, "assistent", "")
    assert "Section target not found" in block


async def test_the_iserror_flag_beats_the_text():
    """With a flag available the text is not searched at all — in either direction.

    `FakeMcp` reports failure the way MCP really does, through the flag, so this
    needs no server of its own any more. The one it used to have built its
    answer by calling back into the base, which now asks the flag — the two
    called each other until the recursion was swallowed by the `except` that
    treats an unreadable note as a missing one, and the test read an empty
    memory as if that were the point.
    """
    note = "- Eine Regel, die das Wort not found enthaelt."
    mcp = FakeMcp({f"{ROOT}/Agent-assistent.md": note})
    assert "not found" in await read_memory(mcp, ROOT, "assistent", "")

    mcp = FakeMcp({f"{ROOT}/Agent-assistent.md": "- harmlos"},
                  fail={"vault__notes_read"})
    assert await read_memory(mcp, ROOT, "assistent", "") == ""


async def test_without_a_vault_it_tells_the_agent(db):
    """Without a folder set, the agent gets a clear refusal instead of an error."""
    u = await make_user(db, "ohnevault")
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "person", "text": "irgendwas"})
    assert out == NO_MEMORY
    assert mcp.calls == []


async def test_remember_addresses_a_note_by_its_plain_path(db):
    """The note tools take a path and nothing around it.

    Traccoon builds the argument itself instead of letting the model do it, and
    this records the shape — the memory ran on a server whose address was an
    object with a discriminator, and the day that server went, every write here
    named a tool that did not exist.
    """
    u = await make_user(db, "merker")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "# Mensch\n\n"})
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "person", "text": "Commit-Betreffe auf Deutsch."})
    assert "Noted" in out
    for name, args in mcp.calls:
        assert name in FakeMcp.KNOWN, f"{name} is not a tool of the note server"
        assert args["path"] == f"{ROOT}/Mensch.md"
    assert "- [" in mcp.notes[f"{ROOT}/Mensch.md"]
    assert "Commit-Betreffe auf Deutsch." in mcp.notes[f"{ROOT}/Mensch.md"]


async def test_remember_creates_a_missing_note(db):
    """The first insight creates the note, with a heading."""
    u = await make_user(db, "erster")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "agent", "text": "Tests mitliefern."},
                                 agent_role="developer")
    assert "Noted" in out
    # Asked first, then written with a heading: appending alone would create the
    # note without one, and a memory note is read by a person in the vault.
    assert mcp.names() == ["vault__notes_read", "vault__notes_write"]
    assert mcp.notes[f"{ROOT}/Agent-developer.md"].startswith("# Agent-developer")
    assert "Tests mitliefern." in mcp.notes[f"{ROOT}/Agent-developer.md"]


async def test_remember_reports_failure(db):
    """If neither works, the agent learns that, instead of feeling safe."""
    u = await make_user(db, "pech")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp(fail={"vault__notes_read", "vault__notes_append", "vault__notes_write"})
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "person", "text": "etwas"})
    assert out.startswith("ERROR")


async def test_a_project_area_without_a_project(db):
    """In a project-less assistant run there is no project memory, and it says so."""
    u = await make_user(db, "projektlos")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": "project", "text": "x"}, agent_role="assistent")
    assert out.startswith("ERROR") and "person" in out
    assert mcp.calls == []


async def test_forget_removes_only_the_matching_line(db):
    """What is outdated falls away, the rest stays."""
    u = await make_user(db, "vergesser")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md":
                   "# Mensch\n\n- [2026-01-01] Commit-Betreffe auf Englisch.\n"
                   "- [2026-01-02] Keine Werbemails melden.\n"})
    out = await call_memory_tool(db, mcp, u.id, "forget",
                                 {"area": "person", "fragment": "Englisch"})
    assert "1 line" in out
    remainder = mcp.notes[f"{ROOT}/Mensch.md"]
    assert "Englisch" not in remainder and "Keine Werbemails melden." in remainder


async def test_forget_without_hits_changes_nothing(db):
    """No hit means: do not write, say so."""
    u = await make_user(db, "trefferlos")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "- [2026-01-01] Eine Vorgabe.\n"})
    out = await call_memory_tool(db, mcp, u.id, "forget",
                                 {"area": "person", "fragment": "gibtsnicht"})
    assert "nothing changed" in out
    assert "vault__notes_write" not in mcp.names()


async def test_search_stays_within_the_memory_folder(db):
    """The search must not rummage through the whole vault."""
    u = await make_user(db, "sucher")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "memory_search", {"query": "Commit"})
    assert "Commit" in out
    name, args = mcp.calls[0]
    assert name == "vault__notes_search"
    # The folder is part of the query language now, and quoted: a vault folder
    # has spaces in its name and would otherwise fall apart into two terms.
    assert args["query"] == f'path:"{ROOT}" Commit'


async def test_the_memory_root_is_empty_without_an_owner(db):
    """A run without a user context has no memory."""
    assert await memory_root(db, None) == ""


# ── Verdrahtung im Lauf ──────────────────────────────────────────────────────

def test_memory_tools_are_always_allowed():
    """`allowed_tools` is deny by default, and the memory tools have to get past it;
    otherwise a freshly created agent silently never learns anything."""
    from app.worker.runtime import AgentDef
    from app.worker.tools_memory import MEMORY_TOOL_NAMES

    a = AgentDef(id=None, name="x", role="x", system_prompt="", provider="claude_code",
                 model="m", token_name="", fallback=None, fallback_model="",
                 fallback_token_name="", temperature=0.3, max_tokens=1024, max_iterations=5,
                 can_code=False, can_read_code=False, can_delegate=False, web_search=False,
                 allowed_tools=[], allowed_skills=[], autoload_skills=[], delegate_to=[])
    assert a.learns is True
    for name in MEMORY_TOOL_NAMES:
        assert a.tool_allowed(name)
    assert not a.tool_allowed("vault__notes_write")


def test_the_learning_switch_comes_from_the_row():
    """`learns=false` on the agent switches lookup and review off."""
    from app.models.agents import AgentDefinition
    from app.worker.runtime import agent_def_from_row

    row = AgentDefinition(role="developer", provider="claude_code", model="m", temperature=0.3,
                          max_tokens=1024, max_turns_planning=5, max_turns_execution=5,
                          can_code=True, can_read_code=True, can_delegate=False,
                          web_search=False, allowed_tools=[], allowed_skills=[],
                          autoload_skills=[], delegate_to=[], learns=False)
    assert agent_def_from_row(row, "execute").learns is False


async def test_the_learning_switch_in_the_api(db, client):
    """The switch is settable over the agent API and is returned."""
    u = await make_user(db, "agentenchef")
    r = await client.post("/agents", headers=auth(u),
                          json={"role": "lerner", "provider": "claude_code"})
    assert r.status_code == 201
    assert r.json()["learns"] is True          # Standard: lernt
    aid = r.json()["id"]
    r = await client.put(f"/agents/{aid}", headers=auth(u),
                         json={"role": "lerner", "provider": "claude_code", "learns": False})
    assert r.status_code == 200 and r.json()["learns"] is False


async def test_the_memory_folder_in_the_api(db, client):
    """Set the folder (with slashes), switch it off again.

    What is checked is the column, not `/me/flags`: the `redis_stub` of the test environment
    cannot serve the flag query.
    """
    u = await make_user(db, "vaultnutzer")
    r = await client.put("/me/vault-memory-path", headers=auth(u), json={"value": f"/{ROOT}/"})
    assert r.status_code == 204
    await db.refresh(u)
    assert u.vault_memory_path == ROOT          # leading and trailing slashes gone
    assert (await client.put("/me/vault-memory-path", headers=auth(u),
                             json={"value": ""})).status_code == 204
    await db.refresh(u)
    assert u.vault_memory_path == ""            # empty = memory off
    assert await memory_root(db, u.id) == ""


# ── Conversation history in the chat ─────────────────────────────────────────

async def test_chat_history(db):
    """The chat carries the most recent exchanges along; old and foreign ones stay outside."""
    import datetime as dt

    from app.models.assistant import AssistantTask
    from app.worker.__main__ import _chat_history

    u = await make_user(db, "plauderer")
    foreign = await make_user(db, "fremder")
    now = dt.datetime.now(tz=dt.timezone.utc)

    def task(**kw):
        d = dict(owner_user_id=u.id, kind="chat", status="done", created_at=now)
        d.update(kw)
        return AssistantTask(**d)

    db.add_all([
        # Too old is now a question of weeks, not of hours: since the conversation memory,
        # older material wanders into the summary instead of falling away without replacement.
        task(title="alt", meta={"chat_text": "Uraltes"}, result="Uralte Antwort",
             created_at=now - dt.timedelta(days=30)),
        task(title="fremd", meta={"chat_text": "Fremdes"}, result="A", owner_user_id=foreign.id),
        task(title="anderer", meta={"chat_text": "Game", "agent": "game-operator"}, result="A"),
        task(title="laufend", meta={"chat_text": "Noch offen"}, status="running"),
        task(title="frueher", meta={"chat_text": "Wie schreibe ich Commits?"},
             result="Auf Deutsch mit TRA-Nummer."),
    ])
    await db.commit()
    current = task(title="jetzt", meta={"chat_text": "Und die Betreffzeile?"}, status="approved")
    db.add(current)
    await db.commit()
    await db.refresh(current)

    history = await _chat_history(db, current)
    texts = " | ".join(v["body"] for v in history)
    assert "Wie schreibe ich Commits?" in texts and "Auf Deutsch mit TRA-Nummer." in texts
    for outside in ("Uraltes", "Fremdes", "Game", "Noch offen", "Und die Betreffzeile?"):
        assert outside not in texts
    assert [v["role"] for v in history] == ["user", "agent"]


async def test_chat_history_kept_separate_per_agent(db):
    """A specialist agent has its own conversation, not that of the assistant."""
    from app.models.assistant import AssistantTask
    from app.worker.__main__ import _chat_history

    u = await make_user(db, "zweigleisig")
    db.add_all([
        AssistantTask(owner_user_id=u.id, kind="chat", status="done", title="a",
                      meta={"chat_text": "Assistenten-Frage"}, result="A1"),
        AssistantTask(owner_user_id=u.id, kind="chat", status="done", title="b",
                      meta={"chat_text": "Game question", "agent": "game-operator"}, result="A2"),
    ])
    await db.commit()
    running = AssistantTask(owner_user_id=u.id, kind="chat", status="approved", title="c",
                            meta={"chat_text": "Weiter", "agent": "game-operator"})
    db.add(running)
    await db.commit()
    await db.refresh(running)

    texts = " | ".join(v["body"] for v in await _chat_history(db, running))
    assert "Game question" in texts and "Assistenten-Frage" not in texts


# ── Review ───────────────────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, text="", tool_calls=None):
        self.text = text
        self.tool_calls = tool_calls or []
        self.usage = {"input_tokens": 10, "output_tokens": 5}
        self.cache_read_tokens = 0
        self.raw = {"choices": [{"message": {"role": "assistant", "content": text,
                                             "tool_calls": []}}]}


class FakeCall:
    def __init__(self, name, arguments, cid="c1"):
        self.name, self.arguments, self.id = name, arguments, cid


async def test_the_review_remembers_and_counts_tokens(db, monkeypatch):
    """The review may remember, and its consumption lands on the counters of the run."""
    from app.worker import runtime
    from app.worker.runtime import AgentDef, _reflect

    u = await make_user(db, "rueckschauer")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "# Mensch\n\n"})

    replies = [
        FakeResp(tool_calls=[FakeCall("remember",
                                      {"area": "person", "text": "Deutsche Commits."})]),
        FakeResp(text="nichts"),
    ]
    seen: list[list[dict]] = []

    async def fake_chat(**kw):
        assert {t["function"]["name"] for t in kw["tools"]} == {
            "remember", "forget", "memory_search"}, "only memory tools"
        seen.append(list(kw["messages"]))
        return replies.pop(0)

    monkeypatch.setattr(runtime.router, "chat", fake_chat)
    agent = AgentDef(id=None, name="developer", role="developer", system_prompt="", provider="p",
                     model="m", token_name="", fallback=None, fallback_model="",
                     fallback_token_name="", temperature=0.3, max_tokens=1024, max_iterations=5,
                     can_code=False, can_read_code=False, can_delegate=False, web_search=False,
                     allowed_tools=[], allowed_skills=[], autoload_skills=[], delegate_to=[])

    log_line: list[tuple] = []

    async def log(role, tool, content):
        log_line.append((role, tool, content))

    ein, aus, cache = await _reflect(db=db, mcp=mcp, agent=agent, owner_id=u.id,
                                     project_key="", messages=[{"role": "user", "content": "x"}],
                                     summary="Habe alles erledigt.", log_line=log,
                                     tokens={}, base_urls={})
    assert (ein, aus, cache) == (20, 10, 0)          # two turns
    assert "Deutsche Commits." in mcp.notes[f"{ROOT}/Mensch.md"]
    assert any(t == "remember" for _r, t, _c in log_line)

    # The assignment has to stand as a user turn at the end: role=system would be rebuilt
    # into a system block at Anthropic and would no longer stand at the end of the
    # conversation. And the closing summary of the run belongs before it: it is its result.
    last = seen[0][-2:]
    assert last[0] == {"role": "assistant", "content": "Habe alles erledigt."}
    assert last[1]["role"] == "user" and "A look back at this run" in last[1]["content"]


async def test_a_review_without_a_lesson_writes_nothing(db, monkeypatch):
    """The normal case: nothing learned, one short turn, no vault write access."""
    from app.worker import runtime
    from app.worker.runtime import AgentDef, _reflect

    u = await make_user(db, "stiller")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()

    async def fake_chat(**kw):
        return FakeResp(text="nichts")

    monkeypatch.setattr(runtime.router, "chat", fake_chat)
    agent = AgentDef(id=None, name="a", role="a", system_prompt="", provider="p", model="m",
                     token_name="", fallback=None, fallback_model="", fallback_token_name="",
                     temperature=0.3, max_tokens=1024, max_iterations=5, can_code=False,
                     can_read_code=False, can_delegate=False, web_search=False,
                     allowed_tools=[], allowed_skills=[], autoload_skills=[], delegate_to=[])

    async def log(*a):
        pass

    await _reflect(db=db, mcp=mcp, agent=agent, owner_id=u.id, project_key="",
                   messages=[], summary="", log_line=log, tokens={}, base_urls={})
    assert mcp.calls == []


async def test_the_review_refuses_foreign_tools(db, monkeypatch):
    """In the review nothing else may happen any more than learning."""
    from app.worker import runtime
    from app.worker.runtime import AgentDef, _reflect

    u = await make_user(db, "schlaumeier")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    replies = [FakeResp(tool_calls=[FakeCall("traccoon_create_issue", {"summary": "x"})]),
                 FakeResp(text="ok")]

    async def fake_chat(**kw):
        return replies.pop(0)

    monkeypatch.setattr(runtime.router, "chat", fake_chat)
    agent = AgentDef(id=None, name="a", role="a", system_prompt="", provider="p", model="m",
                     token_name="", fallback=None, fallback_model="", fallback_token_name="",
                     temperature=0.3, max_tokens=1024, max_iterations=5, can_code=False,
                     can_read_code=False, can_delegate=False, web_search=False,
                     allowed_tools=["traccoon_*"], allowed_skills=[], autoload_skills=[],
                     delegate_to=[])
    reported: list[str] = []

    async def log(role, tool, content):
        reported.append(content)

    await _reflect(db=db, mcp=mcp, agent=agent, owner_id=u.id, project_key="",
                   messages=[], summary="", log_line=log, tokens={}, base_urls={})
    assert any("ERROR" in g for g in reported)
    assert mcp.calls == []


@pytest.mark.parametrize("area", ["quatsch", "", "PERSON "])
async def test_an_unknown_area(db, area):
    """An invented scope writes nowhere."""
    u = await make_user(db, f"bereich{abs(hash(area)) % 1000}")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "x"})
    out = await call_memory_tool(db, mcp, u.id, "remember",
                                 {"area": area, "text": "y"})
    if area.strip().lower() == "person":
        assert "Noted" in out          # case and spaces are forgivable
    else:
        assert out.startswith("ERROR")
        assert mcp.calls == []


# ── Teaching: writing into a foreign memory ──────────────────────────────────

async def test_teaching_writes_into_the_foreign_note(db):
    """The supervision writes a rule into the note of a different role."""
    u = await make_user(db, "aufseher")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_teach_tool(db, mcp, u.id, {
        "agent": "developer", "area": "agent",
        "text": "Vor dem Abschluss immer check laufen lassen."})
    assert "Noted" in out
    note = mcp.notes[f"{ROOT}/Agent-developer.md"]
    assert "check laufen lassen" in note
    # The origin has to be readable in the vault: this rule did not come from a run of that
    # agent, and a wrong judgement of the supervision must be recognisable as one.
    assert "(Aufsicht)" in note


async def test_teaching_into_the_narrowest_note(db):
    """With a project key the rule lands in the project-and-role note."""
    u = await make_user(db, "aufseher2")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_teach_tool(db, mcp, u.id, {
        "agent": "developer", "area": "project_agent", "project": "TRA",
        "text": "Migration und DEV_CREATE_ALL zusammen stempeln."})
    assert "Noted" in out
    assert "stempeln" in mcp.notes[f"{ROOT}/Projekt-TRA-Agent-developer.md"]


@pytest.mark.parametrize("args,hint", [
    ({"area": "agent", "text": "x"}, "agent"),                        # ohne Rolle
    ({"agent": "developer", "area": "person", "text": "x"}, "area"),  # zu weiter Bereich
    ({"agent": "developer", "area": "agent"}, "text"),                # ohne Satz
    ({"agent": "developer", "area": "project_agent", "text": "x"}, "project"),  # ohne Projekt
])
async def test_teaching_refuses_incomplete_calls(db, args, hint):
    """Nothing half-addressed gets written: a rule in the wrong note is worse than none."""
    u = await make_user(db, f"unvollstaendig{abs(hash(hint)) % 1000}")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_teach_tool(db, mcp, u.id, args)
    assert out.startswith("ERROR") and hint in out
    assert mcp.calls == []


def test_teaching_is_not_always_allowed():
    """Unlike the three memory tools, this one needs an explicit entry in `allowed_tools`.

    Otherwise every agent could write into every other agent's memory, and the look back after
    a run (which may call the memory tools) would reach into foreign notes as well.
    """
    from app.worker.runtime import AgentDef
    from app.worker.tools_memory import MEMORY_TOOL_NAMES

    assert TEACH_TOOL_NAME not in MEMORY_TOOL_NAMES

    def agent(tools):
        return AgentDef(id=None, name="x", role="x", system_prompt="", provider="claude_code",
                        model="m", token_name="", fallback=None, fallback_model="",
                        fallback_token_name="", temperature=0.3, max_tokens=1024,
                        max_iterations=5, can_code=False, can_read_code=False,
                        can_delegate=False, web_search=False, allowed_tools=tools,
                        allowed_skills=[], autoload_skills=[], delegate_to=[])

    assert not agent([]).tool_allowed(TEACH_TOOL_NAME)
    assert agent([TEACH_TOOL_NAME]).tool_allowed(TEACH_TOOL_NAME)
