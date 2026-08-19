"""Repository browser plus editor per project (works on the workspace checkout /workspace/<key>).

Reading and writing files, committing (with an auto-generated title and description over an
LLM), showing the branch and pulling or pushing the current branch. Maintainers only, and only with git_enabled.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import ProjectRole
from ..worker import gitops
from ..worker.providers.router import router as llm_router
from ..worker.secrets import resolve_git_token, resolve_provider_token
from .deps import Access, require_role

router = APIRouter(tags=["repo"])

MAX_FILE_BYTES = 1_000_000  # 1 MB Editor-Limit
DEFAULT_MODEL = os.getenv("DEFAULT_CLAUDE_MODEL", "claude-sonnet-4-5")


def _workdir(project) -> str:
    return gitops.project_workdir(project.key)


def _require_git(project) -> str:
    if not project.git_enabled:
        raise HTTPException(409, "Git ist für dieses Projekt nicht aktiv")
    wd = _workdir(project)
    if not os.path.isdir(os.path.join(wd, ".git")):
        raise HTTPException(409, "Repo noch nicht bereit (noch kein Klon/Lauf)")
    return wd


def _safe_path(workdir: str, rel: str) -> str:
    """Pfad zwingend innerhalb des Repos; kein Ausbruch, kein .git."""
    root = os.path.realpath(workdir)
    full = os.path.realpath(os.path.join(root, (rel or "").lstrip("/")))
    if full != root and not full.startswith(root + os.sep):
        raise HTTPException(400, "Ungültiger Pfad")
    first = os.path.relpath(full, root).split(os.sep)[0]
    if first == ".git":
        raise HTTPException(400, ".git ist gesperrt")
    return full


async def _authed(db: AsyncSession, project, owner_id) -> tuple[str, str]:
    """(remote-URL mit Token, roher Token) für Pull/Push."""
    host = urlsplit(project.github_repo).hostname or ""
    token = await resolve_git_token(db, project.git_token_enc, owner_id, host) or ""
    return gitops._authed_url(project.github_repo, token), token


async def _cur_branch(wd: str) -> str:
    _, b = await gitops._git(wd, "rev-parse", "--abbrev-ref", "HEAD")
    return b.strip()


@router.get("/projects/{project_id}/repo/status")
async def repo_status(access: Access = Depends(require_role(ProjectRole.maintainer)),
                      db: AsyncSession = Depends(get_session)):
    p = access.project
    wd = _require_git(p)
    branch = await _cur_branch(wd)
    _, dirty = await gitops._git(wd, "status", "--porcelain")
    dirty_files = [ln[3:] for ln in dirty.splitlines() if ln.strip()]
    ahead = behind = 0
    has_remote = bool(p.github_repo)
    if has_remote and branch:
        rc, ab = await gitops._git(wd, "rev-list", "--left-right", "--count",
                                   f"origin/{branch}...HEAD")
        if rc == 0 and "\t" in ab:
            left, right = ab.split("\t")[:2]
            behind, ahead = int(left or 0), int(right or 0)
    return {"branch": branch, "dirty": dirty_files, "ahead": ahead,
            "behind": behind, "has_remote": has_remote}


@router.get("/projects/{project_id}/repo/tree")
async def repo_tree(access: Access = Depends(require_role(ProjectRole.maintainer)),
                    db: AsyncSession = Depends(get_session)):
    wd = _require_git(access.project)
    _, out = await gitops._git(wd, "ls-files", "--cached", "--others", "--exclude-standard")
    files = sorted({ln for ln in out.splitlines() if ln.strip() and not ln.startswith(".git/")})
    return {"files": files}


@router.get("/projects/{project_id}/repo/file")
async def repo_read(path: str, access: Access = Depends(require_role(ProjectRole.maintainer)),
                    db: AsyncSession = Depends(get_session)):
    full = _safe_path(_require_git(access.project), path)
    if not os.path.isfile(full):
        raise HTTPException(404, "Datei nicht gefunden")
    if os.path.getsize(full) > MAX_FILE_BYTES:
        raise HTTPException(413, "Datei zu groß für den Editor")
    try:
        content = open(full, encoding="utf-8").read()
    except UnicodeDecodeError:
        raise HTTPException(415, "Binärdatei — nicht editierbar")
    return {"path": path, "content": content}


@router.get("/projects/{project_id}/repo/raw")
async def repo_raw(path: str, access: Access = Depends(require_role(ProjectRole.maintainer)),
                   db: AsyncSession = Depends(get_session)):
    """Raw file bytes (for an image preview) with a guessed content type."""
    full = _safe_path(_require_git(access.project), path)
    if not os.path.isfile(full):
        raise HTTPException(404, "Datei nicht gefunden")
    if os.path.getsize(full) > 5_000_000:
        raise HTTPException(413, "Datei zu groß für die Vorschau")
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    with open(full, "rb") as fh:
        return Response(content=fh.read(), media_type=ctype)


class WriteIn(BaseModel):
    path: str
    content: str


@router.put("/projects/{project_id}/repo/file")
async def repo_write(data: WriteIn, access: Access = Depends(require_role(ProjectRole.maintainer)),
                     db: AsyncSession = Depends(get_session)):
    full = _safe_path(_require_git(access.project), data.path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(data.content)
    return {"ok": True}


class CommitIn(BaseModel):
    title: str
    description: str = ""


@router.post("/projects/{project_id}/repo/commit")
async def repo_commit(data: CommitIn, access: Access = Depends(require_role(ProjectRole.maintainer)),
                      db: AsyncSession = Depends(get_session)):
    wd = _require_git(access.project)
    if not data.title.strip():
        raise HTTPException(400, "Commit-Titel fehlt")
    await gitops._git(wd, "add", "-A")
    rc, _ = await gitops._git(wd, "diff", "--cached", "--quiet")
    if rc == 0:
        raise HTTPException(409, "Nichts zu committen")
    msg = data.title.strip()
    if data.description.strip():
        msg += "\n\n" + data.description.strip()
    rc, out = await gitops._git(wd, "commit", "-m", msg)
    if rc != 0:
        raise HTTPException(500, f"Commit fehlgeschlagen: {out[:200]}")
    _, sha = await gitops._git(wd, "rev-parse", "HEAD")
    return {"ok": True, "commit": sha.strip()}


@router.post("/projects/{project_id}/repo/commit-message")
async def repo_commit_message(access: Access = Depends(require_role(ProjectRole.maintainer)),
                              db: AsyncSession = Depends(get_session)):
    """Generate a title plus description from the current diff (LLM, the token of the user)."""
    p = access.project
    wd = _require_git(p)
    await gitops._git(wd, "add", "-A")
    _, diff = await gitops._git(wd, "diff", "--cached")
    if not diff.strip():
        raise HTTPException(409, "Keine Änderungen zum Beschreiben")
    # Prefer the project default subscription, otherwise the personal default.
    tok_name = p.default_token_name if p.default_provider == "claude_code" else ""
    token = await resolve_provider_token(db, access.user.id, "claude_code", tok_name)
    prompt = (
        "Du bekommst einen Git-Diff. Erzeuge einen prägnanten Commit-Titel (Imperativ, "
        "max. 72 Zeichen, Deutsch) und eine kurze Beschreibung (2–4 Zeilen, Stichpunkte ok). "
        "Antworte NUR als JSON: {\"title\": \"...\", \"description\": \"...\"}\n\n"
        f"```diff\n{diff[:15000]}\n```"
    )
    try:
        resp = await llm_router.chat(provider="claude_code", model=DEFAULT_MODEL,
                                     messages=[{"role": "user", "content": prompt}],
                                     temperature=0.2, max_tokens=500,
                                     tokens={"claude_code": token})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LLM-Fehler: {exc}"[:200])
    text = resp.text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return {"title": (data.get("title") or "").strip(),
                    "description": (data.get("description") or "").strip()}
        except json.JSONDecodeError:
            pass
    # Fallback: erste Zeile = Titel, Rest = Beschreibung
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return {"title": lines[0][:72] if lines else "Änderungen", "description": "\n".join(lines[1:])[:600]}


@router.post("/projects/{project_id}/repo/pull")
async def repo_pull(access: Access = Depends(require_role(ProjectRole.maintainer)),
                    db: AsyncSession = Depends(get_session)):
    p = access.project
    wd = _require_git(p)
    branch = await _cur_branch(wd)
    url, token = await _authed(db, p, access.user.id)
    rc, out = await gitops._git(wd, "pull", "--ff-only", url, branch)
    out = gitops._redact(out, token)
    if rc != 0:
        raise HTTPException(409, f"Pull fehlgeschlagen: {out[:300]}")
    return {"ok": True, "output": out[:500]}


@router.post("/projects/{project_id}/repo/push")
async def repo_push(access: Access = Depends(require_role(ProjectRole.maintainer)),
                    db: AsyncSession = Depends(get_session)):
    p = access.project
    wd = _require_git(p)
    branch = await _cur_branch(wd)
    host = urlsplit(p.github_repo).hostname or ""
    token = await resolve_git_token(db, p.git_token_enc, access.user.id, host) or ""
    ctx = gitops.GitCtx(workdir=wd, branch=branch, remote=p.github_repo, token=token,
                        main=p.merge_target or "main", enabled=True)
    if not await gitops._push(ctx, wd, branch):
        raise HTTPException(409, "Push fehlgeschlagen (Auth/Netz/Konflikt)")
    return {"ok": True}
