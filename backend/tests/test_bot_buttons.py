"""Answered Telegram questions carry no buttons any more.

The occasion: after a press the keyboard stayed. In the history it was then no longer visible
which question had already been answered and which was still open, and the old buttons kept
inviting a press.
"""
import pytest
from app.bot.__main__ import _done


class FakeMessage:
    def __init__(self, text="<b>Frage</b>\nInhalt", edit_text_error=False):
        self.html_text = text
        self.message_id = 42
        self.edit_text_error = edit_text_error
        self.processed = None
        self.markup_removed = False

    async def edit_text(self, text, **_kw):
        if self.edit_text_error:
            raise RuntimeError("message is not modified")
        self.processed = text

    async def edit_reply_markup(self, reply_markup=None):
        assert reply_markup is None
        self.markup_removed = True


class FakeCq:
    def __init__(self, message):
        self.message = message


async def test_a_note_replaces_the_keyboard():
    msg = FakeMessage()
    await _done(FakeCq(msg), "✅ Freigegeben")
    # edit_text without reply_markup removes the keyboard with it, and the outcome stands there.
    assert msg.processed.startswith("<b>Frage</b>\nInhalt")
    assert "✅ Freigegeben" in msg.processed
    assert "<i>" in msg.processed


async def test_an_old_message_at_least_loses_its_buttons():
    """Too old or unchanged means the text stays, but the buttons have to go."""
    msg = FakeMessage(edit_text_error=True)
    await _done(FakeCq(msg), "✅ Freigegeben")
    assert msg.markup_removed


async def test_no_crash_without_a_message():
    await _done(FakeCq(None), "✅ Freigegeben")


@pytest.mark.parametrize("raw, expected", [("<b>böse</b>", "&lt;b&gt;"), ("A & B", "&amp;")])
async def test_the_note_is_escaped(raw, expected):
    """The note goes out as HTML: unescaped markup takes the message apart."""
    msg = FakeMessage()
    await _done(FakeCq(msg), raw)
    assert expected in msg.processed
