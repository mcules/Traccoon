"""`_upload_name_typ` does NOT map the `mime_type` delivered by Telegram unchecked onto the
HTTP content type of the multipart part that goes to the Whisper container: `mime_type` is a
metadatum filled freely by the sending client (potentially by an attacker or user), not a
verified server side property. Only known audio MIME types are let through, and everything
else falls back on a safe default.
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
    # Not listed, for instance an exotic or wrong format.
    name, mime = _upload_name_typ("audio", "audio/x-irgendwas")
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_kein_mime_type_faellt_auf_sicheren_default():
    name, mime = _upload_name_typ("audio", None)
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_manipulierter_mime_type_landet_nicht_im_content_type():
    """The actual attack vector: a prepared mime_type with control or special characters must
    NEVER end up one to one as an HTTP content type header value."""
    boesartig = "audio/mpeg\r\nX-Injected: 1"
    name, mime = _upload_name_typ("audio", boesartig)
    assert mime == "audio/mpeg"
    assert "\r" not in mime and "\n" not in mime
    assert name == "audio.mp3"


def test_gross_klein_schreibung_wird_normalisiert():
    name, mime = _upload_name_typ("audio", "AUDIO/MP4")
    assert (name, mime) == ("audio.m4a", "audio/mp4")
