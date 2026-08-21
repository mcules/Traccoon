"""Answered Telegram questions carry no buttons any more.

The occasion: after a press the keyboard stayed. In the history it was then no longer visible
which question had already been answered and which was still open, and the old buttons kept
inviting a press.
"""
import pytest
from app.bot.__main__ import _erledigt


class FakeMessage:
    def __init__(self, text="<b>Frage</b>\nInhalt", edit_text_error=False):
        self.html_text = text
        self.message_id = 42
        self.edit_text_error = edit_text_error
        self.bearbeitet = None
        self.markup_entfernt = False

    async def edit_text(self, text, **_kw):
        if self.edit_text_error:
            raise RuntimeError("message is not modified")
        self.bearbeitet = text

    async def edit_reply_markup(self, reply_markup=None):
        assert reply_markup is None
        self.markup_entfernt = True


class FakeCq:
    def __init__(self, message):
        self.message = message


async def test_vermerk_ersetzt_die_tastatur():
    msg = FakeMessage()
    await _erledigt(FakeCq(msg), "✅ Freigegeben")
    # edit_text without reply_markup removes the keyboard with it, and the outcome stands there.
    assert msg.bearbeitet.startswith("<b>Frage</b>\nInhalt")
    assert "✅ Freigegeben" in msg.bearbeitet
    assert "<i>" in msg.bearbeitet


async def test_alte_message_verliert_wenigstens_die_knoepfe():
    """Too old or unchanged means the text stays, but the buttons have to go."""
    msg = FakeMessage(edit_text_error=True)
    await _erledigt(FakeCq(msg), "✅ Freigegeben")
    assert msg.markup_entfernt


async def test_ohne_message_kein_absturz():
    await _erledigt(FakeCq(None), "✅ Freigegeben")


@pytest.mark.parametrize("roh,erwartet", [("<b>böse</b>", "&lt;b&gt;"), ("A & B", "&amp;")])
async def test_vermerk_wird_maskiert(roh, erwartet):
    """The note goes out as HTML: unescaped markup takes the message apart."""
    msg = FakeMessage()
    await _erledigt(FakeCq(msg), roh)
    assert erwartet in msg.bearbeitet
