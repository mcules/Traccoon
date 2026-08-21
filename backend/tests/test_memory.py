"""Memory of the agents in the Obsidian vault (TRA-30).

The most important test here is `test_erinnere_dich_baut_target_als_objekt`: the obsidian MCP
describes `target` as a `oneOf` without a `type`, and older models send a string for it
instead of an object, and then every call ends in `MCP error -32602`. Because Traccoon builds
the argument itself, that cannot happen here; the test records it.
"""
import pytest
from conftest import auth, make_user

from app.worker.tools_memory import (
    NO_MEMORY, call_memory_tool, memory_root, note_path, read_memory,
)


class FakeMcp:
    """MCP session replacement: records calls and answers get_note out of `notes`."""

    def __init__(self, notes: dict[str, str] | None = None, fail: set[str] | None = None):
        self.notes = dict(notes or {})
        self.fail = fail or set()
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        if name in self.fail:
            return "MCP error -32000: absichtlich fehlgeschlagen"
        if name == "obsidian__obsidian_get_note":
            path = args["target"]["path"]
            if path not in self.notes:
                return "Error: file not found"
            return self.notes[path]
        if name == "obsidian__obsidian_write_note":
            path = args["target"]["path"]
            if path in self.notes and not args.get("overwrite"):
                return "Error: file_exists"
            self.notes[path] = args["content"]
            return "geschrieben"
        if name == "obsidian__obsidian_append_to_note":
            path = args["target"]["path"]
            if path not in self.notes:
                return "Error: not found"
            self.notes[path] += args["content"]
            return "angehängt"
        if name == "obsidian__obsidian_search_notes":
            return f"Treffer für {args.get('query')} unter {args.get('pathPrefix')}"
        return "(kein Output)"

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


ROOT = "04 Traccoon/Gedächtnis"


def test_paths():
    """Every area has its note; areas that do not fit yield no path."""
    assert note_path(ROOT, "mensch") == f"{ROOT}/Mensch.md"
    assert note_path(ROOT, "agent", "developer") == f"{ROOT}/Agent-developer.md"
    assert note_path(ROOT, "projekt", "developer", "TRA") == f"{ROOT}/Projekt-TRA.md"
    # Without a role respectively a project the note does not exist: the caller has to take 'mensch'.
    assert note_path(ROOT, "agent") is None
    assert note_path(ROOT, "projekt", "developer") is None
    # No folder configured means the function is off.
    assert note_path("", "mensch") is None
    assert note_path("  ", "mensch") is None


def test_paths_without_a_path_change():
    """Role and project key must not be able to leave the folder."""
    p = note_path(ROOT, "projekt", "", "../../etc")
    assert p is not None and ".." not in p and p.count("/") == ROOT.count("/") + 1


async def test_recall_collects_from_the_general_to_the_specific():
    """All three notes land in the block, the specific one last."""
    mcp = FakeMcp({
        f"{ROOT}/Mensch.md": "- Commit-Betreffe auf Deutsch.",
        f"{ROOT}/Agent-developer.md": "- Immer Tests mitliefern.",
        f"{ROOT}/Projekt-TRA.md": "- Migration in beiden Tracks pflegen.",
    })
    text = await read_memory(mcp, ROOT, "developer", "TRA")
    assert text.index("Commit-Betreffe") < text.index("Tests mitliefern") \
        < text.index("Migration in beiden")
    assert "## Über deinen Menschen" in text and "## Für dieses Projekt" in text


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
    assert len(await read_memory(mcp, ROOT, "", "")) <= 6000


async def test_without_a_vault_it_tells_the_agent(db):
    """Without a folder set, the agent gets a clear refusal instead of an error."""
    u = await make_user(db, "ohnevault")
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "erinnere_dich",
                                 {"bereich": "mensch", "text": "irgendwas"})
    assert out == NO_MEMORY
    assert mcp.calls == []


async def test_remember_builds_the_target_as_an_object(db):
    """REGRESSION: `target` has to be an object; a string is `MCP error -32602`.

    Exactly on that older models fail when they call the obsidian MCP themselves. Because
    Traccoon builds the argument, that must never happen here.
    """
    u = await make_user(db, "merker")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "# Mensch\n\n"})
    out = await call_memory_tool(db, mcp, u.id, "erinnere_dich",
                                 {"bereich": "mensch", "text": "Commit-Betreffe auf Deutsch."})
    assert "Gemerkt" in out
    for _name, args in mcp.calls:
        assert isinstance(args["target"], dict), "target as a string, MCP error -32602"
        assert args["target"]["type"] == "path"
    assert "- [" in mcp.notes[f"{ROOT}/Mensch.md"]
    assert "Commit-Betreffe auf Deutsch." in mcp.notes[f"{ROOT}/Mensch.md"]


async def test_remember_creates_a_missing_note(db):
    """The first insight creates the note; appending alone fails on that."""
    u = await make_user(db, "erster")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "erinnere_dich",
                                 {"bereich": "agent", "text": "Tests mitliefern."},
                                 agent_role="developer")
    assert "Gemerkt" in out
    assert mcp.names() == ["obsidian__obsidian_append_to_note", "obsidian__obsidian_write_note"]
    assert "Tests mitliefern." in mcp.notes[f"{ROOT}/Agent-developer.md"]


async def test_remember_reports_failure(db):
    """If neither works, the agent learns that, instead of feeling safe."""
    u = await make_user(db, "pech")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp(fail={"obsidian__obsidian_append_to_note", "obsidian__obsidian_write_note"})
    out = await call_memory_tool(db, mcp, u.id, "erinnere_dich",
                                 {"bereich": "mensch", "text": "etwas"})
    assert out.startswith("FEHLER")


async def test_a_project_area_without_a_project(db):
    """In a project-less assistant run there is no project memory, and it says so."""
    u = await make_user(db, "projektlos")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "erinnere_dich",
                                 {"bereich": "projekt", "text": "x"}, agent_role="assistent")
    assert out.startswith("FEHLER") and "mensch" in out
    assert mcp.calls == []


async def test_forget_removes_only_the_matching_line(db):
    """What is outdated falls away, the rest stays."""
    u = await make_user(db, "vergesser")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md":
                   "# Mensch\n\n- [2026-01-01] Commit-Betreffe auf Englisch.\n"
                   "- [2026-01-02] Keine Werbemails melden.\n"})
    out = await call_memory_tool(db, mcp, u.id, "vergiss",
                                 {"bereich": "mensch", "textfragment": "Englisch"})
    assert "1 Zeile" in out
    remainder = mcp.notes[f"{ROOT}/Mensch.md"]
    assert "Englisch" not in remainder and "Keine Werbemails melden." in remainder


async def test_forget_without_hits_changes_nothing(db):
    """No hit means: do not write, say so."""
    u = await make_user(db, "trefferlos")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "- [2026-01-01] Eine Vorgabe.\n"})
    out = await call_memory_tool(db, mcp, u.id, "vergiss",
                                 {"bereich": "mensch", "textfragment": "gibtsnicht"})
    assert "nichts geändert" in out
    assert "obsidian__obsidian_write_note" not in mcp.names()


async def test_search_stays_within_the_memory_folder(db):
    """The search must not rummage through the whole vault."""
    u = await make_user(db, "sucher")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp()
    out = await call_memory_tool(db, mcp, u.id, "gedaechtnis_suchen", {"suche": "Commit"})
    assert "Commit" in out
    name, args = mcp.calls[0]
    assert name == "obsidian__obsidian_search_notes"
    assert args["pathPrefix"] == ROOT and args["mode"] == "text"


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
    assert not a.tool_allowed("obsidian__obsidian_write_note")


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
        task(title="anderer", meta={"chat_text": "UniWar", "agent": "uniwar-operator"}, result="A"),
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
    for outside in ("Uraltes", "Fremdes", "UniWar", "Noch offen", "Und die Betreffzeile?"):
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
                      meta={"chat_text": "UniWar-Frage", "agent": "uniwar-operator"}, result="A2"),
    ])
    await db.commit()
    running = AssistantTask(owner_user_id=u.id, kind="chat", status="approved", title="c",
                            meta={"chat_text": "Weiter", "agent": "uniwar-operator"})
    db.add(running)
    await db.commit()
    await db.refresh(running)

    texts = " | ".join(v["body"] for v in await _chat_history(db, running))
    assert "UniWar-Frage" in texts and "Assistenten-Frage" not in texts


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
        FakeResp(tool_calls=[FakeCall("erinnere_dich",
                                      {"bereich": "mensch", "text": "Deutsche Commits."})]),
        FakeResp(text="nichts"),
    ]
    seen: list[list[dict]] = []

    async def fake_chat(**kw):
        assert {t["function"]["name"] for t in kw["tools"]} == {
            "erinnere_dich", "vergiss", "gedaechtnis_suchen"}, "only memory tools"
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
    assert any(t == "erinnere_dich" for _r, t, _c in log_line)

    # The assignment has to stand as a user turn at the end: role=system would be rebuilt
    # into a system block at Anthropic and would no longer stand at the end of the
    # conversation. And the closing summary of the run belongs before it: it is its result.
    last = seen[0][-2:]
    assert last[0] == {"role": "assistant", "content": "Habe alles erledigt."}
    assert last[1]["role"] == "user" and "Rückschau" in last[1]["content"]


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
    assert any("FEHLER" in g for g in reported)
    assert mcp.calls == []


@pytest.mark.parametrize("area", ["quatsch", "", "MENSCH "])
async def test_an_unknown_area(db, area):
    """An invented scope writes nowhere."""
    u = await make_user(db, f"bereich{abs(hash(area)) % 1000}")
    u.vault_memory_path = ROOT
    await db.commit()
    mcp = FakeMcp({f"{ROOT}/Mensch.md": "x"})
    out = await call_memory_tool(db, mcp, u.id, "erinnere_dich",
                                 {"bereich": area, "text": "y"})
    if area.strip().lower() == "mensch":
        assert "Gemerkt" in out          # case and spaces are forgivable
    else:
        assert out.startswith("FEHLER")
        assert mcp.calls == []
