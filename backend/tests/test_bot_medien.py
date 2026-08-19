"""The notifier sends a medium along when one lies there, and otherwise text exactly as before.

The way out to Telegram is the notifier in the telegram-bot process, and it is the only one:
the backend container lacks `TELEGRAM_BOT_TOKEN` entirely. That is why the media path hangs
off the notification (`media_path`) and not off a second sender.

What is tested is `_zustellen` directly (no aiogram bot, no real notification), following the
pattern of `test_bot_buttons.py`: a FakeBot instead of a network, a small dummy object instead of an ORM.
"""
import datetime as dt

import pytest

from app.bot.__main__ import _gif_masse, _zustellen


# The smallest valid GIF89a, set by hand: 480x270, one graphic control with a delay of 100
# hundredths (= 1 s), no colour tables. A real GIF as a fixture would be mere ballast for the
# question "are the dimensions read?".
def _gif(breite=480, hoehe=270, hundertstel=100) -> bytes:
    return (b"GIF89a"
            + breite.to_bytes(2, "little") + hoehe.to_bytes(2, "little")
            + bytes([0x00, 0x00, 0x00])                       # no global colour table
            + b"\x21\xf9\x04\x00" + hundertstel.to_bytes(2, "little") + b"\x00\x00"
            + b"\x2c" + b"\x00" * 8 + b"\x00"                 # Bildbeschreibung
            + b"\x02\x02\x44\x01\x00"                         # LZW size plus one sub-block
            + b"\x3b")


class FakeBot:
    def __init__(self):
        self.animation = None
        self.nachricht = None
        self.foto = None

    async def send_animation(self, chat_id, **kw):
        self.animation = (chat_id, kw)

    async def send_photo(self, chat_id, **kw):
        self.foto = (chat_id, kw)

    async def send_message(self, chat_id, text, **kw):
        self.nachricht = (chat_id, text, kw)


class FakeNotification:
    """Only the fields `_zustellen` touches."""

    def __init__(self, media_path=None, media_kind=None):
        self.id = 7
        self.chat_id = "4711"
        self.media_path = media_path
        self.media_kind = media_kind
        self.notified_at = None


MARKUP = object()   # stands for the inline keyboard; `_zustellen` only passes it through


async def test_medium_geht_als_animation_mit_beschriftung(tmp_path):
    datei = tmp_path / "buero.gif"
    datei.write_bytes(_gif())
    bot, n = FakeBot(), FakeNotification(str(datei), "animation")

    await _zustellen(bot, n, "<b>Feierabend</b>", MARKUP)

    chat_id, kw = bot.animation
    assert chat_id == 4711 and bot.nachricht is None
    assert kw["caption"] == "<b>Feierabend</b>" and kw["parse_mode"] == "HTML"
    # The dimensions come from the file, not from a constant: Telegram sizes the bubble from
    # them BEFORE the GIF is loaded.
    assert (kw["width"], kw["height"], kw["duration"]) == (480, 270, 1)
    assert n.notified_at is not None


async def test_ohne_medium_bleibt_der_textweg_unveraendert():
    bot, n = FakeBot(), FakeNotification()

    await _zustellen(bot, n, "<b>Ticket</b>\nfertig", MARKUP)

    chat_id, text, kw = bot.nachricht
    assert (chat_id, text) == (4711, "<b>Ticket</b>\nfertig")
    assert kw["parse_mode"] == "HTML" and bot.animation is None
    assert n.notified_at is not None


async def test_fehlende_datei_faellt_still_auf_text_zurueck(tmp_path):
    """A film that is not there must not swallow a message."""
    bot, n = FakeBot(), FakeNotification(str(tmp_path / "gibt-es-nicht.gif"), "animation")

    await _zustellen(bot, n, "<b>Feierabend</b>", MARKUP)

    assert bot.animation is None
    assert bot.nachricht[1] == "<b>Feierabend</b>"
    # And acknowledged regardless: otherwise the poller retries the same row every 3 s forever.
    assert isinstance(n.notified_at, dt.datetime)


@pytest.mark.parametrize("pfad_da", [True, False])
async def test_tastatur_kommt_auf_beiden_wegen_mit(tmp_path, pfad_da):
    datei = tmp_path / "buero.gif"
    if pfad_da:
        datei.write_bytes(_gif())
    bot, n = FakeBot(), FakeNotification(str(datei) if pfad_da else None, "animation")

    await _zustellen(bot, n, "Frage", MARKUP)

    gesendet = bot.animation[1] if pfad_da else bot.nachricht[2]
    assert gesendet["reply_markup"] is MARKUP


async def test_sendefehler_setzt_notified_at_trotzdem():
    class KaputterBot(FakeBot):
        async def send_message(self, *a, **kw):
            raise RuntimeError("Telegram does not answer")

    n = FakeNotification()
    await _zustellen(KaputterBot(), n, "Text", None)
    assert n.notified_at is not None


async def test_beschriftung_wird_auf_1024_gekappt(tmp_path):
    """Telegram rejects captions that are too long, and then nothing would arrive at all."""
    datei = tmp_path / "buero.gif"
    datei.write_bytes(_gif())
    bot, n = FakeBot(), FakeNotification(str(datei), "animation")

    await _zustellen(bot, n, "x" * 2000, None)

    assert len(bot.animation[1]["caption"]) == 1024


async def test_media_kind_photo_nimmt_den_foto_weg(tmp_path):
    datei = tmp_path / "bild.png"
    datei.write_bytes(b"\x89PNG\r\n\x1a\n")
    bot, n = FakeBot(), FakeNotification(str(datei), "photo")

    await _zustellen(bot, n, "Bild", None)

    assert bot.foto is not None and bot.animation is None


def test_gif_masse_schweigt_bei_fremdem_format():
    """No GIF means no claim about dimensions; Telegram then measures itself."""
    assert _gif_masse(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40) == {}
    assert _gif_masse(b"") == {}


def test_gif_masse_summiert_alle_verzoegerungen():
    # Two frames at 1.5 s give 3 s. A single frame GIF (0) gets no duration at all.
    doppelt = _gif(hundertstel=150)[:-1] + _gif(hundertstel=150)[13:]
    assert _gif_masse(doppelt)["duration"] == 3
    assert "duration" not in _gif_masse(_gif(hundertstel=0))
