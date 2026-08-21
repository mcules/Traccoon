"""Error texts an interface can show in its own language.

An `HTTPException` carries a sentence and nothing else, so the browser can only print what
the server wrote: English, even for somebody whose interface is German. Moving the sentences
into the frontend catalog alone does not work either, because the API is read by things that
have no catalog at all (the bot, the agent tools, whoever calls it with curl) and they would
be left with a bare key.

`Fehler` carries both. `detail` stays the English sentence, so every existing reader of the
API keeps what it had. Next to it travel the key of that sentence and the values that were
filled into it; a browser looks the key up in its catalog and shows its own wording, and
falls back to the English detail when it does not know the key.

The text here is a template, not an f-string: `Fehler(404, "err.x", "No {was}", was="board")`.
That way the placeholders of the English sentence and of every translation are the same set
of names, and a translator can move them around inside their sentence.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def insert(text: str, values: dict[str, str]) -> str:
    """Fill `{name}` placeholders. Unknown ones stay as they are: a text with a stray brace
    reads oddly, but no error message dies on the way to the person who needs it."""
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


class Fehler(HTTPException):
    """An HTTPException that names its text."""

    def __init__(self, status_code: int, key: str, text: str, **values: Any) -> None:
        self.key = key
        self.values = {name: str(value) for name, value in values.items()}
        super().__init__(status_code, insert(text, self.values))


async def error_handler(_request: Request, exc: Fehler) -> JSONResponse:
    """Put the key beside the text. Everything else stays as FastAPI would send it."""
    inhalt: dict[str, Any] = {"detail": exc.detail, "key": exc.key}
    if exc.values:
        inhalt["werte"] = exc.values
    return JSONResponse(status_code=exc.status_code, content=inhalt,
                        headers=getattr(exc, "headers", None))
