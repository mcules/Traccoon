"""Bytes that are not UTF-8, and the run they used to kill at the very end.

On 2026-08-24 a three-word chat message to the assistant came back as nothing but

    'utf-8' codec can't encode character '\\udcc5' in position 75839: surrogates not allowed

Position 75839 in a prompt that a three-word message did not write: the bad character came in
with something the run had READ. `\\udcc5` is not a character at all — it is what Python leaves
behind when it decodes the byte `0xC5` (`Å` in cp1252) with `errors="surrogateescape"`, the way
Linux hands over filenames and the way a foreign MCP server may have decoded a mail body.

The expensive part is WHERE it fell over: `str.encode("utf-8")` refuses a lone surrogate, and
that is the HTTP boundary to the provider — after the context was gathered, the tools had run
and the model time was paid for. So the tests below guard two things: that such a string never
reaches the encode, and that the log says which tool it came from. Without the second one the
next occurrence is the same detective work from scratch.
"""
import logging

import pytest

from app.worker.providers.base import ChatResponse, ToolCall
from app.worker.providers.router import Router
from app.worker.text import MojibakeWatch, has_surrogates, repair_messages, scrub_surrogates

from test_office_worker import agentdef, answer, make_run, no_redis  # noqa: F401

# The offender itself: the byte 0xC5, kept alive as a lone surrogate.
BAD = "a\udcc5b"


# ── 1. Das Werkzeug selbst ───────────────────────────────────────────────────

def test_the_scrub_produces_something_that_can_be_encoded():
    with pytest.raises(UnicodeEncodeError):
        BAD.encode("utf-8")          # so steht der Fehler im Chat

    clean = scrub_surrogates(BAD)
    assert clean.encode("utf-8")     # und so nicht mehr
    # The evidence stays readable: whoever sees this in the log can recognise a mojibake
    # filename. `errors="ignore"` would have swallowed the byte without a trace.
    assert "\\udcc5" in clean and clean.startswith("a") and clean.endswith("b")


def test_text_without_surrogates_comes_back_unchanged():
    """The normal case has to cost nothing — and change nothing."""
    plain = "Völlig normaler Text mit Ümlauten und 🦝, 80 kB davon."
    assert scrub_surrogates(plain) is plain
    assert not has_surrogates(plain)


def test_the_watch_reports_once_per_source():
    watch = MojibakeWatch()
    assert watch.clean(BAD, "fs_list").encode("utf-8")
    assert watch.clean(BAD, "fs_list").encode("utf-8")
    assert watch._seen == {"fs_list"}


# ── 2. Ein Werkzeug-Ergebnis reisst den Lauf nicht mehr ab ───────────────────

class _BadMcp:
    """An MCP server that answers with a filename in the wrong encoding."""

    def __init__(self, result: str = BAD):
        self.result = result

    async def list_tools(self):
        return []

    async def call(self, name, args):
        return self.result


async def test_a_tool_result_with_a_lone_surrogate_does_not_stop_the_run(make_run, caplog):
    """The whole point: the answer arrives instead of a red error card."""
    with caplog.at_level(logging.WARNING):
        result, seen = await make_run(
            [answer(calls=[ToolCall(id="t1", name="obsidian__obsidian_list_notes",
                                    arguments={})]),
             answer("fertig")],
            mcp=_BadMcp(f"Notizen/{BAD}.md"))

    assert result.status == "done" and result.text == "fertig"

    # And what went to the provider on the second turn really is sendable — that is the
    # place the old run died.
    messages = seen[1]["messages"]
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert tool_msg["content"].encode("utf-8")
    assert "\\udcc5" in tool_msg["content"]


async def test_the_warning_names_the_tool_once_not_once_per_message(make_run, caplog):
    """Twenty calls of one tool must not write twenty identical lines — but the name has to
    be in there, because the codec error itself points at the encode and never at the origin."""
    with caplog.at_level(logging.WARNING):
        result, _ = await make_run(
            [answer(calls=[ToolCall(id="t1", name="imap__search_emails", arguments={})]),
             answer(calls=[ToolCall(id="t2", name="imap__search_emails", arguments={})]),
             answer("fertig")],
            mcp=_BadMcp(), agent=agentdef(allowed_tools=["*"]))

    assert result.status == "done"
    hits = [r for r in caplog.records if "Undecodable bytes" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in hits]
    assert "imap__search_emails" in hits[0].getMessage()
    # The excerpt has to carry the evidence, otherwise the line says nothing.
    assert "udcc5" in hits[0].getMessage()


# ── 3. Die letzte Verteidigungslinie am Provider ─────────────────────────────

async def test_the_router_repairs_what_slipped_through_and_says_so(monkeypatch, caplog):
    """A source nobody thought of must not be able to take a run down — but it must not stay
    invisible either, otherwise the scrub hides where the mojibake came from."""
    router = Router()
    sent = {}

    class _Provider:
        async def chat(self, **kw):
            sent.update(kw)
            # The place the old error came from: what the provider builds gets encoded.
            for m in kw["messages"]:
                str(m).encode("utf-8")
            return ChatResponse(text="ok")

    monkeypatch.setattr(router, "_impl", lambda *a, **k: _Provider())
    messages = [{"role": "user", "content": f"Datei {BAD}"}]

    with caplog.at_level(logging.WARNING):
        resp = await router.chat(provider="claude_code", model="sonnet", messages=messages)

    assert resp.text == "ok"
    # Repaired IN PLACE: the caller keeps this list as the history of the run and sends it
    # again every round. A copy would be clean once and the original would carry the bad byte
    # into every following turn.
    assert messages[0]["content"].encode("utf-8")
    assert any("unscrubbed" in r.getMessage() for r in caplog.records)


def test_repair_reports_nothing_when_there_is_nothing_to_do():
    messages = [{"role": "user", "content": "alles gut"},
                {"role": "tool", "name": "fs_list", "content": ["a", {"text": "b"}]}]
    assert repair_messages(messages) == ""
    assert messages[1]["content"] == ["a", {"text": "b"}]


# ── 4. Die Naht am MCP-Client ────────────────────────────────────────────────

async def test_the_mcp_client_cleans_a_text_block(monkeypatch):
    """The earliest seam: whoever calls `call`/`call_ex` outside the runtime (tools_memory,
    flows) is protected too."""
    from app.worker.mcp_client import McpSession

    session = McpSession("http://example.invalid", "")

    async def fake_rpc(method, params):
        return {"content": [{"type": "text", "text": f"Notiz {BAD}"}]}

    monkeypatch.setattr(session, "_rpc", fake_rpc)
    text, is_error = await session.call_ex("obsidian__obsidian_get_note", {})
    assert text.encode("utf-8") and "\\udcc5" in text and not is_error
