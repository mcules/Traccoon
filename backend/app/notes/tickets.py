"""A short ticket for the requests a browser makes without a header.

A picture inside a note is an `<img src>`, a compiled template arrives through
`import()`, and a service worker fetches without any JavaScript of ours running
at all. None of those carry an `Authorization` header — the browser decides what
goes on such a request, and it sends cookies and nothing else.

So there is a second, much smaller credential: a signed string that says who and
until when, and means nothing beyond reading this one person's notes.
Deliberately not a JWT — that would be a second thing that looks like a session,
and the day somebody feeds it to the session check is the day this becomes a
hole. This has one meaning and no parser worth attacking.

It lives here, in a module that imports nothing of the application, because both
halves of the note area need it: the bridge that still answers `/api/notes`, and
the routes that have moved to `/api/notes-native`. A cookie path does not span
those two — `/api/notes` matches `/api/notes/…` and not `/api/notes-native/…`,
which is a rule about slashes and not about intent.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from ..config import settings

COOKIE = "notes_asset"
TTL = 12 * 3600
# Both halves of the note area. The cookie is set once per path; a browser sends
# whichever matches the request.
PATHS = ("/api/notes", "/api/notes-native")

_PURPOSE = "notes-asset:"


def _sign(body: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"{_PURPOSE}{body}".encode(),
                    hashlib.sha256).hexdigest()


def issue(user_id: int) -> str:
    body = f"{user_id}.{int(time.time()) + TTL}"
    return f"{body}.{_sign(body)}"


def holder(token: str) -> int | None:
    """Whose ticket this is, or None if it is not one, or not any more."""
    try:
        uid_s, exp_s, sig = token.split(".", 2)
        if not hmac.compare_digest(sig, _sign(f"{uid_s}.{exp_s}")):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return int(uid_s)
    except (ValueError, AttributeError):
        return None
