"""The approved plan belongs in the execution.

`_process` passed `plan` through to `run_agent`, but it was used only in planning mode: the
developer worked from the ticket description. On one ticket that was a symptom report ("find
the cause, evaluate `job_runs`") while the approved plan had long named the cause, with the
file and the line number. On 2026-08-07 that cost three runs and 155 turns without a line of
code: the agent worked the finished analysis out a second time itself. The planning phase is
worthless when its result does not reach the execution.
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


async def _messages(db, monkeypatch, mode: str, plan: str) -> list[dict]:
    """Builds the prompt up to the first model turn and returns the messages."""
    _, proj, issue, _ = await _project_with_ticket(db)
    seen: list[dict] = []

    async def fake_chat(**kw):
        seen.extend(kw["messages"])
        raise RuntimeError("stop")       # after building the prompt that is enough

    monkeypatch.setattr(runtime.router, "chat", fake_chat)
    try:
        await runtime.run_agent(
            db=db, agent=_Agent(),
            issue={"id": issue.id, "key": issue.key, "summary": "Job schlägt fehl",
                   "description": "## Symptom\nSeit dem 03.08. jeden Tag error.", "plan": plan},
            project={"id": proj.id, "key": proj.key, "system_prompt": "", "stack_dir": "",
                     "live_url": ""},
            mode=mode, permissions={}, ws_root="", gate_on=False, tokens={})
    except Exception:  # noqa: BLE001 - the abort is intended
        pass
    return seen


PLAN = "## Ursache\n`__main__.py:554` wertet `loop_exhausted` als Fehler.\n## Umsetzung\n1. …"


async def test_execution_receives_the_plan(monkeypatch, db):
    texts = " ".join(m.get("content") or "" for m in await _messages(db, monkeypatch, "execute", PLAN))

    assert "approved implementation plan" in texts
    assert "__main__.py:554" in texts, "the plan itself is missing from the prompt"
    assert "Work it through" in texts


async def test_no_empty_section_without_a_plan(monkeypatch, db):
    texts = " ".join(m.get("content") or "" for m in await _messages(db, monkeypatch, "execute", ""))

    assert "Freigegebener Umsetzungsplan" not in texts


async def test_a_planning_run_keeps_its_own_wording(monkeypatch, db):
    """In the planning the plan is revised, not worked off."""
    texts = " ".join(m.get("content") or "" for m in await _messages(db, monkeypatch, "plan", PLAN))

    assert "existing plan" in texts
    assert "Freigegebener Umsetzungsplan" not in texts
