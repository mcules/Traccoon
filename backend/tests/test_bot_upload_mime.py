"""`_upload_name_typ` does NOT map the `mime_type` delivered by Telegram unchecked onto the
HTTP content type of the multipart part that goes to the Whisper container: `mime_type` is a
metadatum filled freely by the sending client (potentially by an attacker or user), not a
verified server side property. Only known audio MIME types are let through, and everything
else falls back on a safe default.
"""
from app.bot.__main__ import _upload_name_kind


def test_a_video_note_is_always_mp4():
    name, mime = _upload_name_kind("video_note", "irrelevant/egal")
    assert (name, mime) == ("video_note.mp4", "video/mp4")


def test_voice_is_always_ogg():
    name, mime = _upload_name_kind("voice", "irgendwas/anderes")
    assert (name, mime) == ("voice.ogg", "audio/ogg")


def test_a_known_audio_mime_is_passed_through():
    name, mime = _upload_name_kind("audio", "audio/mp4")
    assert (name, mime) == ("audio.m4a", "audio/mp4")


def test_the_mp3_variant_is_normalised():
    name, mime = _upload_name_kind("audio", "audio/mpeg")
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_an_unknown_mime_falls_back_to_a_safe_default():
    # Not listed, for instance an exotic or wrong format.
    name, mime = _upload_name_kind("audio", "audio/x-irgendwas")
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_no_mime_type_falls_back_to_a_safe_default():
    name, mime = _upload_name_kind("audio", None)
    assert (name, mime) == ("audio.mp3", "audio/mpeg")


def test_a_tampered_mime_type_does_not_reach_the_content_type():
    """The actual attack vector: a prepared mime_type with control or special characters must
    NEVER end up one to one as an HTTP content type header value."""
    malicious = "audio/mpeg\r\nX-Injected: 1"
    name, mime = _upload_name_kind("audio", malicious)
    assert mime == "audio/mpeg"
    assert "\r" not in mime and "\n" not in mime
    assert name == "audio.mp3"


def test_upper_and_lower_case_are_normalised():
    name, mime = _upload_name_kind("audio", "AUDIO/MP4")
    assert (name, mime) == ("audio.m4a", "audio/mp4")
