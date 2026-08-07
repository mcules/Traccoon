"""`_upload_name_typ` bildet den von Telegram gelieferten `mime_type` NICHT ungeprüft auf den
HTTP-Content-Type des Multipart-Teils ab, das an den Whisper-Container geht — `mime_type` ist
ein vom sendenden Client frei befülltes Metadatum (potentiell vom Angreifer/Nutzer), keine
verifizierte serverseitige Eigenschaft. Nur bekannte Audio-MIME-Typen werden durchgelassen,
alles andere fällt auf einen sicheren Default zurück.
"""
from app.bot.__main__ import _upload_name_typ


def test_video_note_ist_immer_mp4():
    name, mime = _upload_name_typ("video_note", "irrelevant/egal")
    assert (name, mime) == ("video_note.mp4", "video/mp4")


def test_voice_ist_immer_ogg():
    name, mime = _upload_name_typ("voice", "irgendwas/anderes")
    assert (name, mime) == ("voice.ogg", "audio/ogg")


def test_bekannter_audio_mime_wird_durchgereicht():
    name, mime = _upload_name_typ("audio", "audio/mp4")
    assert (name, mime) == ("audio.m4a", "audio/mp4")


def test_mp3_variante_wird_normiert():
    name, mime = _upload_name_typ("audio", "audio/mpeg")
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_unbekannter_mime_faellt_auf_sicheren_default():
    # Nicht gelistet — z. B. ein exotisches/falsches Format.
    name, mime = _upload_name_typ("audio", "audio/x-irgendwas")
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_kein_mime_type_faellt_auf_sicheren_default():
    name, mime = _upload_name_typ("audio", None)
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_manipulierter_mime_type_landet_nicht_im_content_type():
    """Der eigentliche Angriffsvektor: ein präparierter mime_type mit Kontroll-/
    Sonderzeichen darf NIEMALS 1:1 als HTTP-Content-Type-Header-Wert enden."""
    boesartig = "audio/mpeg\r\nX-Injected: 1"
    name, mime = _upload_name_typ("audio", boesartig)
    assert mime == "audio/mpeg"
    assert "\r" not in mime and "\n" not in mime
    assert name == "audio.mp3"


def test_gross_klein_schreibung_wird_normalisiert():
    name, mime = _upload_name_typ("audio", "AUDIO/MP4")
    assert (name, mime) == ("audio.m4a", "audio/mp4")
