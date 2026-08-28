"""The description of a project is knowledge, and knowledge belongs in the run.

Three project texts reach an agent: the house rules out of the worktree, the instruction out
of `system_prompt` and, from here on, the description. The description was the only one that
went nowhere. It is where what the project IS gets written down (its stack, the interfaces it
hangs on, what is still open), and an agent had to read all of that out of the code again on
every single run.
"""
from app.worker import runtime
from test_lifecycle_process import _project_with_ticket


class _Agent:
    id = None
    role = name = "developer"
    system_prompt = ""
    provider, model, token_name = "claude_code", "claude-sonnet-5", ""
    fallback, fallback_model, fallback_token_name = None, "", ""
    temperature, max_tokens, max_iterations = 0.3, 16384, 80
    can_code = can_read_code = True
    can_delegate = web_search = False
    allowed_tools: list = []
    allowed_skills: list = []
    autoload_skills: list = []
    delegate_to: list = []
    learns = False
    max_context_tokens = None
    effort = ""


DESCRIPTION = ("Cabrillo logs of a radio contest, evaluated in the browser.\n"
               "Backend: TypeScript and Express on SQLite.")


async def _messages(db, monkeypatch, description: str, system_prompt: str = "") -> list[dict]:
    """Builds the prompt up to the first model turn and returns the messages."""
    _, proj, issue, _ = await _project_with_ticket(db)
    seen: list[dict] = []

    async def fake_chat(**kw):
        seen.extend(kw["messages"])
        raise RuntimeError("stop")       # the prompt is built, that is what this is about

    monkeypatch.setattr(runtime.router, "chat", fake_chat)
    try:
        await runtime.run_agent(
            db=db, agent=_Agent(),
            issue={"id": issue.id, "key": issue.key, "summary": "Scoring is wrong",
                   "description": "The multiplier counts twice.", "plan": None},
            project={"id": proj.id, "key": proj.key, "description": description,
                     "system_prompt": system_prompt, "stack_dir": "", "live_url": ""},
            mode="execute", permissions={}, ws_root="", gate_on=False, tokens={})
    except Exception:  # noqa: BLE001 - the abort is intended
        pass
    return seen


async def test_the_description_reaches_the_agent(monkeypatch, db):
    texts = " ".join(m.get("content") or "" for m in await _messages(db, monkeypatch, DESCRIPTION))

    assert "TypeScript and Express on SQLite" in texts, "the project description is missing"
    assert "not an assignment" in texts, "without that an agent works off what it reads there"


async def test_nothing_stands_there_without_a_description(monkeypatch, db):
    """An empty description costs no section: an empty heading only asks questions."""
    texts = " ".join(m.get("content") or "" for m in await _messages(db, monkeypatch, ""))

    assert "What this project is" not in texts


async def test_the_instruction_keeps_the_last_word(monkeypatch, db):
    """Knowledge first, instruction after. The one that comes later is the one that counts,
    and `system_prompt` says how work is done here."""
    msgs = await _messages(db, monkeypatch, DESCRIPTION, system_prompt="Never touch main.")
    order = [i for i, m in enumerate(msgs)
             if "What this project is" in (m.get("content") or "")
             or "Never touch main." in (m.get("content") or "")]

    assert len(order) == 2, "both texts belong in the prompt"
    assert "What this project is" in (msgs[order[0]].get("content") or "")


async def test_the_worker_hands_the_description_over(db, monkeypatch):
    """The prompt can only carry what the worker puts into the project dict.

    Checked on the correction round of the review gate, because that is the call site that
    already has a harness. The dict is written out per call, so a forgotten key costs nothing
    but the knowledge, silently.
    """
    import app.worker.__main__ as worker
    from app.worker.runtime import RunResult

    _, proj, issue, _ = await _project_with_ticket(db)
    proj.description = DESCRIPTION
    await db.commit()
    seen: list[dict] = []

    async def fake_run_agent(**kw):
        seen.append(kw["project"])
        role = kw["agent"].role
        if role != "code_reviewer":
            return RunResult("done", "corrected")
        # Findings once, then clean: otherwise the gate turns rounds that say nothing here.
        return (RunResult("done", "1. foo.ts:12 — missing null check")
                if sum(1 for p in seen) == 1 else RunResult("done", "<review-ok/>"))

    diffs = {"n": 0}

    async def fake_diff(_ctx):
        diffs["n"] += 1                  # a correction that takes effect changes the diff
        return f"--- a\n+++ b\n+x\n+round {diffs['n']}\n"

    async def fake_load_agent(_db, role, *a, **k):
        class A:
            pass
        agent = A()
        agent.role = role
        return agent

    async def fake_flag(_name, *a, **k):
        return False

    monkeypatch.setattr(worker, "get_flag", fake_flag)
    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    monkeypatch.setattr(worker.gitops, "diff_text", fake_diff)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)

    class _Ctx:
        pass

    await worker._review_gate(
        db, proj, issue, await fake_load_agent(db, "developer"), "/ws", False, {}, [],
        RunResult("done", "done"), _Ctx(), owner_id=None, task_id="t-1", base_urls={})

    corrections = [p for p in seen if p.get("system_prompt") is not None and "description" in p]
    assert corrections, "no run was handed a project dict with a description"
    assert any(p.get("description") == DESCRIPTION for p in corrections)


async def test_a_long_description_is_shortened_visibly(monkeypatch, db):
    long_text = "x" * (runtime.MAX_PROJECT_DESCRIPTION_CHARS + 500)
    texts = " ".join(m.get("content") or "" for m in await _messages(db, monkeypatch, long_text))

    assert "(shortened)" in texts, "a silently halved description would be the worst variant"
