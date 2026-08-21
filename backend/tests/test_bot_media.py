"""The notifier sends a medium along when one lies there, and otherwise text exactly as before.

The way out to Telegram is the notifier in the telegram-bot process, and it is the only one:
the backend container lacks `TELEGRAM_BOT_TOKEN` entirely. That is why the media path hangs
off the notification (`media_path`) and not off a second sender.

What is tested is `_zustellen` directly (no aiogram bot, no real notification), following the
pattern of `test_bot_buttons.py`: a FakeBot instead of a network, a small dummy object instead of an ORM.
"""
import datetime as dt

import pytest

from app.bot.__main__ import _gif_mass, _deliver


# The smallest valid GIF89a, set by hand: 480x270, one graphic control with a delay of 100
# hundredths (= 1 s), no colour tables. A real GIF as a fixture would be mere ballast for the
# question "are the dimensions read?".
def _gif(width=480, height=270, hundredth=100) -> bytes:
    return (b"GIF89a"
            + width.to_bytes(2, "little") + height.to_bytes(2, "little")
            + bytes([0x00, 0x00, 0x00])                       # no global colour table
            + b"\x21\xf9\x04\x00" + hundredth.to_bytes(2, "little") + b"\x00\x00"
            + b"\x2c" + b"\x00" * 8 + b"\x00"                 # Bildbeschreibung
            + b"\x02\x02\x44\x01\x00"                         # LZW size plus one sub-block
            + b"\x3b")


class FakeBot:
    def __init__(self):
        self.animation = None
        self.message = None
        self.photo = None

    async def send_animation(self, chat_id, **kw):
        self.animation = (chat_id, kw)

    async def send_photo(self, chat_id, **kw):
        self.photo = (chat_id, kw)

    async def send_message(self, chat_id, text, **kw):
        self.message = (chat_id, text, kw)


class FakeNotification:
    """Only the fields `_zustellen` touches."""

    def __init__(self, media_path=None, media_kind=None):
        self.id = 7
        self.chat_id = "4711"
        self.media_path = media_path
        self.media_kind = media_kind
        self.notified_at = None


MARKUP = object()   # stands for the inline keyboard; `_zustellen` only passes it through


async def test_media_goes_as_an_animation_with_a_caption(tmp_path):
    file = tmp_path / "buero.gif"
    file.write_bytes(_gif())
    bot, n = FakeBot(), FakeNotification(str(file), "animation")

    await _deliver(bot, n, "<b>Feierabend</b>", MARKUP)

    chat_id, kw = bot.animation
    assert chat_id == 4711 and bot.message is None
    assert kw["caption"] == "<b>Feierabend</b>" and kw["parse_mode"] == "HTML"
    # The dimensions come from the file, not from a constant: Telegram sizes the bubble from
    # them BEFORE the GIF is loaded.
    assert (kw["width"], kw["height"], kw["duration"]) == (480, 270, 1)
    assert n.notified_at is not None


async def test_without_media_the_text_path_stays_unchanged():
    bot, n = FakeBot(), FakeNotification()

    await _deliver(bot, n, "<b>Ticket</b>\nfertig", MARKUP)

    chat_id, text, kw = bot.message
    assert (chat_id, text) == (4711, "<b>Ticket</b>\nfertig")
    assert kw["parse_mode"] == "HTML" and bot.animation is None
    assert n.notified_at is not None


async def test_a_missing_file_quietly_falls_back_to_text(tmp_path):
    """A film that is not there must not swallow a message."""
    bot, n = FakeBot(), FakeNotification(str(tmp_path / "gibt-es-nicht.gif"), "animation")

    await _deliver(bot, n, "<b>Feierabend</b>", MARKUP)

    assert bot.animation is None
    assert bot.message[1] == "<b>Feierabend</b>"
    # And acknowledged regardless: otherwise the poller retries the same row every 3 s forever.
    assert isinstance(n.notified_at, dt.datetime)


@pytest.mark.parametrize("path_there", [True, False])
async def test_the_keyboard_comes_along_on_both_paths(tmp_path, path_there):
    file = tmp_path / "buero.gif"
    if path_there:
        file.write_bytes(_gif())
    bot, n = FakeBot(), FakeNotification(str(file) if path_there else None, "animation")

    await _deliver(bot, n, "Frage", MARKUP)

    sent = bot.animation[1] if path_there else bot.message[2]
    assert sent["reply_markup"] is MARKUP


async def test_a_send_failure_still_sets_notified_at():
    class BrokenBot(FakeBot):
        async def send_message(self, *a, **kw):
            raise RuntimeError("Telegram does not answer")

    n = FakeNotification()
    await _deliver(BrokenBot(), n, "Text", None)
    assert n.notified_at is not None


async def test_the_caption_is_capped_at_1024(tmp_path):
    """Telegram rejects captions that are too long, and then nothing would arrive at all."""
    file = tmp_path / "buero.gif"
    file.write_bytes(_gif())
    bot, n = FakeBot(), FakeNotification(str(file), "animation")

    await _deliver(bot, n, "x" * 2000, None)

    assert len(bot.animation[1]["caption"]) == 1024


async def test_media_kind_photo_takes_the_photo_path(tmp_path):
    file = tmp_path / "bild.png"
    file.write_bytes(b"\x89PNG\r\n\x1a\n")
    bot, n = FakeBot(), FakeNotification(str(file), "photo")

    await _deliver(bot, n, "Bild", None)

    assert bot.photo is not None and bot.animation is None


def test_gif_measuring_stays_quiet_on_a_foreign_format():
    """No GIF means no claim about dimensions; Telegram then measures itself."""
    assert _gif_mass(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40) == {}
    assert _gif_mass(b"") == {}


def test_gif_measuring_sums_all_delays():
    # Two frames at 1.5 s give 3 s. A single frame GIF (0) gets no duration at all.
    duplicate = _gif(hundredth=150)[:-1] + _gif(hundredth=150)[13:]
    assert _gif_mass(duplicate)["duration"] == 3
    assert "duration" not in _gif_mass(_gif(hundredth=0))
