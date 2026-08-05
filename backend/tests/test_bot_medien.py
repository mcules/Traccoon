"""Der Notifier schickt ein Medium mit, wenn eines daliegt — und sonst genau wie bisher Text.

Der Ausgang nach Telegram ist der Notifier im telegram-bot-Prozess, und zwar als einziger:
dem backend-Container fehlt `TELEGRAM_BOT_TOKEN` vollständig. Deshalb hängt der Medienweg
an der Notification (`media_path`) und nicht an einem zweiten Absender.

Getestet wird `_zustellen` direkt (kein aiogram-Bot, keine echte Notification) — nach dem
Muster von `test_bot_buttons.py`: FakeBot statt Netz, kleines Attrappen-Objekt statt ORM.
"""
import datetime as dt

import pytest

from app.bot.__main__ import _gif_masse, _zustellen


# Kleinstes gültiges GIF89a, von Hand gesetzt: 480×270, eine Bildsteuerung mit
# 100 Hundertstel Verzögerung (= 1 s), keine Farbtabellen. Ein echtes GIF als Fixture
# wäre für die Frage „werden die Maße gelesen?" nur Ballast.
def _gif(breite=480, hoehe=270, hundertstel=100) -> bytes:
    return (b"GIF89a"
            + breite.to_bytes(2, "little") + hoehe.to_bytes(2, "little")
            + bytes([0x00, 0x00, 0x00])                       # keine globale Farbtabelle
            + b"\x21\xf9\x04\x00" + hundertstel.to_bytes(2, "little") + b"\x00\x00"
            + b"\x2c" + b"\x00" * 8 + b"\x00"                 # Bildbeschreibung
            + b"\x02\x02\x44\x01\x00"                         # LZW-Größe + ein Teilblock
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
    """Nur die Felder, die `_zustellen` anfasst."""

    def __init__(self, media_path=None, media_kind=None):
        self.id = 7
        self.chat_id = "4711"
        self.media_path = media_path
        self.media_kind = media_kind
        self.notified_at = None


MARKUP = object()   # steht für die Inline-Tastatur; `_zustellen` reicht sie nur durch


async def test_medium_geht_als_animation_mit_beschriftung(tmp_path):
    datei = tmp_path / "buero.gif"
    datei.write_bytes(_gif())
    bot, n = FakeBot(), FakeNotification(str(datei), "animation")

    await _zustellen(bot, n, "<b>Feierabend</b>", MARKUP)

    chat_id, kw = bot.animation
    assert chat_id == 4711 and bot.nachricht is None
    assert kw["caption"] == "<b>Feierabend</b>" and kw["parse_mode"] == "HTML"
    # Maße kommen aus der Datei, nicht aus einer Konstante — Telegram dimensioniert die
    # Blase daraus, BEVOR das GIF geladen ist.
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
    """Ein Film, der nicht da ist, darf keine Nachricht verschlucken."""
    bot, n = FakeBot(), FakeNotification(str(tmp_path / "gibt-es-nicht.gif"), "animation")

    await _zustellen(bot, n, "<b>Feierabend</b>", MARKUP)

    assert bot.animation is None
    assert bot.nachricht[1] == "<b>Feierabend</b>"
    # Und trotzdem quittiert: sonst versucht der Poller dieselbe Zeile alle 3 s endlos neu.
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
            raise RuntimeError("Telegram antwortet nicht")

    n = FakeNotification()
    await _zustellen(KaputterBot(), n, "Text", None)
    assert n.notified_at is not None


async def test_beschriftung_wird_auf_1024_gekappt(tmp_path):
    """Telegram weist zu lange Bildunterschriften ab — dann käme gar nichts an."""
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
    """Kein GIF → keine Behauptung über Maße; Telegram misst dann selbst."""
    assert _gif_masse(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40) == {}
    assert _gif_masse(b"") == {}


def test_gif_masse_summiert_alle_verzoegerungen():
    # Zwei Bilder à 1,5 s → 3 s. Ein Einzelbild-GIF (0) bekommt gar keine Dauer.
    doppelt = _gif(hundertstel=150)[:-1] + _gif(hundertstel=150)[13:]
    assert _gif_masse(doppelt)["duration"] == 3
    assert "duration" not in _gif_masse(_gif(hundertstel=0))
