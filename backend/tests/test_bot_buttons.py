"""Beantwortete Telegram-Fragen tragen keine Knöpfe mehr.

Anlass: Nach einem Druck blieb die Tastatur stehen. Im Verlauf war dann nicht mehr zu sehen,
welche Frage schon beantwortet war und welche noch offen — und die alten Knöpfe luden weiter
zum Drücken ein.
"""
import pytest
from app.bot.__main__ import _erledigt


class FakeMessage:
    def __init__(self, text="<b>Frage</b>\nInhalt", edit_text_fehler=False):
        self.html_text = text
        self.message_id = 42
        self.edit_text_fehler = edit_text_fehler
        self.bearbeitet = None
        self.markup_entfernt = False

    async def edit_text(self, text, **_kw):
        if self.edit_text_fehler:
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
    # edit_text ohne reply_markup entfernt die Tastatur mit — und der Ausgang steht dran.
    assert msg.bearbeitet.startswith("<b>Frage</b>\nInhalt")
    assert "✅ Freigegeben" in msg.bearbeitet
    assert "<i>" in msg.bearbeitet


async def test_alte_nachricht_verliert_wenigstens_die_knoepfe():
    """Zu alt/unverändert → Text bleibt, aber die Knöpfe müssen weg."""
    msg = FakeMessage(edit_text_fehler=True)
    await _erledigt(FakeCq(msg), "✅ Freigegeben")
    assert msg.markup_entfernt


async def test_ohne_nachricht_kein_absturz():
    await _erledigt(FakeCq(None), "✅ Freigegeben")


@pytest.mark.parametrize("roh,erwartet", [("<b>böse</b>", "&lt;b&gt;"), ("A & B", "&amp;")])
async def test_vermerk_wird_maskiert(roh, erwartet):
    """Der Vermerk geht als HTML raus — ungeschütztes Markup zerlegt die Nachricht."""
    msg = FakeMessage()
    await _erledigt(FakeCq(msg), roh)
    assert erwartet in msg.bearbeitet
