"""The note workspace, answered here rather than passed on.

This is the port growing in next to the bridge, not replacing it. `api/notes.py`
keeps serving `/api/notes/*` from the old service and the interface keeps using
it; this router answers the same questions under `/api/notes-native/*` so the two
can be asked the same thing and their answers compared, route by route, against
the real vault.

Nothing points at it yet. When a route here matches the old one on every case
that was recorded, the bridge stops forwarding that one, and when the last one
has moved the bridge and this prefix both go and what is left is `/api/notes`.

Read only, on purpose. The vault is mounted read only in this service, and that
was a deliberate decision back when the other program still wrote into it. It
stays until the writing side here has been through review: an untested writer on
somebody's notes is not a thing to switch on quietly.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import PlainTextResponse

from ..config import settings
from ..core.error import Error
from ..models.user import User
from ..notes import paths
from ..notes.index.links import LinkGraph
from ..notes.index.watch import watch
from ..notes.query.run import run as run_search
from ..notes.query.words import WordIndex
from ..notes.vault.files import Vault, content_hash, is_text, mime_for
from .deps import get_current_user

log = logging.getLogger("notes")

router = APIRouter(prefix="/notes-native", tags=["notes"])

# The link graph, per vault. Reading six thousand notes on every request is not
# a cache decision, it is the difference between an answer and a timeout.
#
# Built when a vault is first asked about and followed from then on. Building it
# on first use rather than at startup keeps a vault that nobody opens out of the
# way; the watcher next to it is what stops the copy from drifting away from the
# disk, which is a failure that says nothing while it happens.
_graphs: dict[str, LinkGraph] = {}
_words: dict[str, WordIndex] = {}
_watchers: dict[str, asyncio.Task] = {}


def graph_of(v: Vault) -> LinkGraph:
    key = str(v.root)
    graph = _graphs.get(key)
    if graph is None:
        graph = LinkGraph()
        graph.build(v)
        _graphs[key] = graph
        index = WordIndex()
        index.build(graph.docs, graph.headings)
        _words[key] = index
    task = _watchers.get(key)
    if task is None or task.done():
        try:
            _watchers[key] = asyncio.create_task(watch(v, graph))
        except RuntimeError:
            # No loop running: a test, or a script importing this. The graph is
            # still correct, it just will not follow the disk, and saying so in
            # the log is better than refusing to answer.
            log.warning("notes: no event loop, the index will not follow %s", key)
    return graph


def vault_of(user: User) -> Vault:
    """The vault of the person asking, or a clear no.

    One vault per person: what is personal hangs off its owner here, the same way
    stores and mail accounts already do. An account without one has no note area,
    which is the state every account starts in.
    """
    rel = (user.vault_path or "").strip()
    if not rel:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_no_vault",
                    "This account has no note vault")
    root = Path(rel)
    if not root.is_dir():
        raise Error(status.HTTP_503_SERVICE_UNAVAILABLE, "err.notes_vault_missing",
                    "The note vault is not there: {path}", path=rel)
    return Vault(root, name=settings.notes_vault_name or root.name)


def _guard(fn, rel: str):
    try:
        return fn()
    except paths.OutsideVault:
        # Deliberately the same answer as for a file that is not there. Telling
        # the two apart would say whether a path exists outside the vault, which
        # is exactly what somebody probing would want to learn.
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=rel) from None
    except FileNotFoundError:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=rel) from None


@router.get("/files/")
async def tree(user: User = Depends(get_current_user)) -> dict:
    return vault_of(user).tree().as_json()


@router.get("/files/content")
async def content(path: str = Query(...), user: User = Depends(get_current_user)):
    """A note as text, or a file as bytes.

    The hash travels with the text: the editor keeps it as the identity of the
    version it is working on and hands it back when it saves, which is how a save
    tells "still the file I read" from "somebody got there first".
    """
    v = vault_of(user)
    if is_text(path):
        text = _guard(lambda: v.read_text(path), path)
        return {"path": path, "content": text, "encoding": "utf8",
                "hash": content_hash(text)}
    data = _guard(lambda: v.read_bytes(path), path)
    return Response(content=data, media_type=mime_for(path))


@router.get("/files/stat")
async def stat(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    v = vault_of(user)
    return {"path": path, **_guard(lambda: v.stat(path), path)}


@router.get("/tags")
async def tags(user: User = Depends(get_current_user)) -> dict:
    return {"tags": graph_of(vault_of(user)).all_tags()}


@router.get("/backlinks")
async def backlinks(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    return {"path": path, "backlinks": graph_of(vault_of(user)).backlinks(path)}


@router.get("/resolve")
async def resolve_link(target: str = Query(...),
                       user: User = Depends(get_current_user)) -> dict:
    """Turn a link into a file, or say that it points at nothing yet.

    A link with an extension that is not a note (`[[Foo.canvas]]`) is not in the
    graph, which only holds notes, so the file index answers for those. A bare
    `[[Foo]]` deliberately does not fall through to it: it should stay unresolved
    so the interface can offer to create the note.
    """
    v = vault_of(user)
    found = graph_of(v).resolve(target)
    if found is None and "." in PurePosixPath(target).name:
        suffix = PurePosixPath(target).suffix.lower()
        if suffix and suffix not in (".md", ".markdown"):
            treffer = v.by_basename().get(PurePosixPath(target).name.lower())
            found = treffer[0] if treffer else None
    return {"target": target, "path": found}


@router.get("/search")
async def search(q: str = Query(""), user: User = Depends(get_current_user)) -> dict:
    v = vault_of(user)
    graph = graph_of(v)
    hits = run_search(graph, q, _words.get(str(v.root)))
    return {"query": q, "hits": [h.as_json() for h in hits]}


@router.get("/health", response_class=PlainTextResponse)
async def health(user: User = Depends(get_current_user)) -> str:
    """Enough to see that the vault is reachable and how much is in it."""
    v = vault_of(user)
    count = len(paths.walk(v.root))
    return f"{v.name}: {count} files"
