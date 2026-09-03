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
from pydantic import BaseModel

from ..config import settings
from ..core.error import Error
from ..models.user import User
from ..notes import paths
from ..notes.dv import index as dv_index
from ..notes.dv import settings as note_settings
from ..notes.dv import tasks as dv_tasks
from ..notes.dv.bases import run as dv_bases
from ..notes.dv.dql import evaluate_inline, execute as run_query, file_object
from ..notes.dv.pages import PageIndex
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
# The richer index the three little languages read: properties, tasks, list
# items, the link graph in both directions. Built beside the search index and
# followed by the same watcher, because two indexes over one folder that are
# refreshed at different moments answer differently about the same note.
_pages: dict[str, PageIndex] = {}
_watchers: dict[str, asyncio.Task] = {}
_settings_loaded = False


def _load_settings(v: Vault) -> None:
    """Take over how the notes were configured to behave, once.

    Held per process rather than per vault, which is right while there is one.
    When a second person gets a vault this moves into the database with the rest
    of the configuration — that is the step the plan calls the one-off takeover,
    and until it happens a second vault would silently inherit the first one's
    checkbox characters.
    """
    global _settings_loaded
    if _settings_loaded:
        return
    dirs = {"query": settings.notes_query_settings_dir,
            "tasks": settings.notes_task_settings_dir}
    note_settings.load(Path(v.root), {k: d for k, d in dirs.items() if d})
    _settings_loaded = True


def graph_of(v: Vault) -> LinkGraph:
    key = str(v.root)
    graph = _graphs.get(key)
    if graph is None:
        _load_settings(v)
        graph = LinkGraph()
        graph.build(v)
        _graphs[key] = graph
        index = WordIndex()
        index.build(graph.docs, graph.headings)
        _words[key] = index
        _pages[key] = dv_index.build(v)
    task = _watchers.get(key)
    if task is None or task.done():
        try:
            pages = _pages[key]

            def follow(rel: str, gone: bool, _v: Vault = v,
                       _p: PageIndex = pages) -> None:
                dv_index.update(_v, _p, rel, removed=gone)

            _watchers[key] = asyncio.create_task(watch(v, graph, also=follow))
        except RuntimeError:
            # No loop running: a test, or a script importing this. The graph is
            # still correct, it just will not follow the disk, and saying so in
            # the log is better than refusing to answer.
            log.warning("notes: no event loop, the index will not follow %s", key)
    return graph


def pages_of(v: Vault) -> PageIndex:
    """The page index of this vault, built and followed on first use."""
    graph_of(v)
    return _pages[str(v.root)]


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


# ---------------------------------------------------------- the three little
# ------------------------------------------------------------------ languages

class QueryIn(BaseModel):
    query: str = ""
    # The note the block sits in. A query says `this.file` and means the note
    # around it, so the same text in two notes is two questions.
    path: str | None = None


class TasksIn(BaseModel):
    query: str = ""


class InlineOne(BaseModel):
    expr: str = ""
    path: str | None = None


class InlineIn(BaseModel):
    expr: str = ""
    path: str | None = None
    items: list[InlineOne] | None = None


@router.post("/dataview/query")
async def query(body: QueryIn, user: User = Depends(get_current_user)) -> dict:
    """Run one query block. A broken query answers with its own error rather
    than a 500: it is the person who wrote it who can fix it."""
    return run_query(pages_of(vault_of(user)), body.query, body.path)


@router.post("/dataview/tasks")
async def task_query(body: TasksIn, user: User = Depends(get_current_user)) -> dict:
    return dv_tasks.execute(pages_of(vault_of(user)), body.query)


@router.post("/dataview/inline")
async def inline(body: InlineIn, user: User = Depends(get_current_user)) -> dict:
    """Evaluate `= expression` against the note it sits in.

    Takes one or a list of them: a note with twenty should cost one round trip
    and not twenty.
    """
    index = pages_of(vault_of(user))
    if body.items is not None:
        return {"results": [evaluate_inline(index, it.expr, it.path)
                            for it in body.items[:500]]}
    return evaluate_inline(index, body.expr, body.path)


@router.get("/dataview/settings")
async def language_settings(user: User = Depends(get_current_user)) -> dict:
    """How the two languages are configured, so the interface shows a value the
    way the notes were written to expect it."""
    vault_of(user)
    return {"query": note_settings.query_settings(),
            "tasks": note_settings.task_settings(),
            "statuses": note_settings.statuses()}


@router.get("/dataview/pages")
async def pages(source: str = Query(""), user: User = Depends(get_current_user)) -> dict:
    """The notes themselves. `source` narrows to a folder (`"03 Areas"`) or a
    tag, so a block scoped to one folder does not drag the vault over the wire."""
    index = pages_of(vault_of(user))
    listed = index.all()
    text = source.strip()
    if text.startswith('"') and text.endswith('"') and len(text) > 1:
        folder = text[1:-1].rstrip("/")
        if folder:
            listed = [p for p in listed
                      if p.path == folder or p.path.startswith(folder + "/")]
    elif text.startswith("#"):
        want = text.lower()
        listed = [p for p in listed if any(t.lower() == want
                                           or t.lower().startswith(want + "/")
                                           for t in p.tags)]
    return {"pages": [{"file": file_object(index, p), "fields": p.fields}
                      for p in listed],
            "total": len(index.pages)}


@router.get("/dataview/page")
async def one_page(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    index = pages_of(vault_of(user))
    p = index.get(path) or index.by_link(path)
    if p is None:
        return {"page": None}
    return {"page": {"file": file_object(index, p), "fields": p.fields}}


@router.get("/dataview/meta")
async def page_meta(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    """Headings with their line numbers, which is what lets a link to a heading
    land on the right line."""
    index = pages_of(vault_of(user))
    p = index.get(path) or index.by_link(path)
    if p is None:
        return {"path": None}
    return {"path": p.path, "headings": p.headings, "tags": p.tags,
            "frontmatter": p.fields}


@router.get("/bases/view")
async def base_view(path: str = Query(...), view: str | None = Query(None),
                    user: User = Depends(get_current_user)) -> dict:
    """A table file, run over the same notes the query language reads."""
    v = vault_of(user)
    source = _guard(lambda: v.read_text(path), path)
    return dv_bases.run(pages_of(v), source, view)
