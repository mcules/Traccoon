"""What a personal access token may reach.

**Deny by default.** A route that no scope names is reachable only with `full`. That is the
whole point of the table: a new endpoint is never accidentally exposed to a token that was
issued before it existed. The opposite arrangement, a `require_scope(...)` dependency hung
on the routes a scope covers, cannot do that. A route without the dependency would simply
have no check at all, so every endpoint added later would be open to every token, and the
mistake would be invisible (nothing to see in the diff, no failing test).

The table is keyed by the **route template** (`/issues/{key}`), not by the request path.
FastAPI puts the matched route into `request.scope["route"]`, so `/issues/ABC-7` and
`/issues/XYZ-3` are the same entry here and a token cannot be widened by a clever path.

A JWT session is never measured against this: the web interface would otherwise have to
learn scopes it does not need. `scopes is None` means "logged in as a human", full stop.
"""
from __future__ import annotations

ASSISTANT = "assistant"
TICKETS = "tickets"
FULL = "full"

# What may be handed out on a create call, in the order the interface offers them.
ALL_SCOPES: tuple[str, ...] = (ASSISTANT, TICKETS, FULL)

# (method or None for any, route template). A pattern ending in `*` matches by prefix,
# everything else has to be equal.
GRANTS: dict[str, tuple[tuple[str | None, str], ...]] = {
    ASSISTANT: (
        (None, "/assistant/*"),
        ("GET", "/auth/me"),
        (None, "/notifications*"),
        # The office socket. Without it the live stream is the one thing a token cannot do,
        # and a client silently degrades to polling.
        (None, "/ws"),
        # The personal channel (new mail, the counter in the bar). Same reasoning as
        # `/notifications*`: it carries what concerns the person, not a project.
        (None, "/ws/me"),
    ),
    TICKETS: (
        ("GET", "/projects"),
        ("GET", "/projects/{project_id}/issues"),
        ("POST", "/projects/{project_id}/issues"),
        ("GET", "/issues/{key}"),
        ("POST", "/issues/{key}/comments"),
    ),
}


def parse(raw: str | None) -> set[str]:
    """The stored comma separated string turned into a set. Unknown names are dropped: a
    scope this version does not know cannot be enforced, so it must not grant anything."""
    return {s for s in (part.strip() for part in (raw or "").split(",")) if s in ALL_SCOPES}


def clean(names: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """The scopes of a create call, filtered to the known ones and put in a fixed order."""
    wanted = {str(n).strip().lower() for n in names}
    return [s for s in ALL_SCOPES if s in wanted]


def _matches(pattern: str, path: str) -> bool:
    if pattern.endswith("*"):
        return path.startswith(pattern[:-1])
    return path == pattern


def grants(scope: str, method: str, path: str) -> bool:
    """Does this ONE scope reach this route?"""
    for wanted_method, pattern in GRANTS.get(scope, ()):
        if (wanted_method is None or wanted_method == method) and _matches(pattern, path):
            return True
    return False


def allowed(scopes: set[str] | None, method: str, path: str) -> bool:
    """May a request with these scopes reach this route?

    `None` is a JWT session and therefore unrestricted. `full` implies the others and is
    checked first, so adding a scope never has to be mirrored into the `full` case.
    """
    if scopes is None or FULL in scopes:
        return True
    return any(grants(scope, method, path) for scope in scopes)
