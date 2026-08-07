## Telegram-Sprachnachrichten an den Assistenten (2026-08-07)

Anschluss an „Der Assistent ‚ignorierte' Nachrichten …" (2026-07-29): Sprachnachrichten
(`voice`, `audio`, `video_note`) fallen nicht mehr in den Auffang-Handler und werden nicht
mehr abgelehnt. Sie werden lokal über einen `faster-whisper`-Container (kein Cloud-Aufruf,
kein Audio verlässt das Haus) transkribiert, das Transkript wird sichtbar als
„🎙 verstanden: …" zurückgemeldet und danach GENAU wie eingehender Text über denselben
Weg (`_chat_auftrag` → `create_chat_task`) an den Assistenten übergeben — gleicher Chat,
gleiche Freigabe-Gates, gleicher Reply-Bezug. Zu lang/zu groß/nicht transkribierbar → ehrliche
Absage mit Grund statt Stille. Umsetzung: `backend/app/bot/__main__.py`
(`_transkribieren`, `_voice_transkript`, `_voice_chat`, `_reply`), neuer Compose-Dienst
`whisper` (`onerahmet/openai-whisper-asr-webservice`, ENV `WHISPER_URL`/`WHISPER_MODEL`).
Kein Vault-Umweg, keine Berührung mit Predecessor/Job #7 — Sprachnachrichten laufen ausschließlich
über den Telegram-Bot direkt an den Assistenten.
