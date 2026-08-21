from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.enums import ProjectRole
from ..models.project import Project
from ..models.ticket import Attachment, Issue, TicketFileChange
from ..models.user import User
from .deps import Access, build_access, get_current_user
from .issues import get_issue_access

router = APIRouter(tags=["files"])


@router.get("/issues/{key}/files")
async def file_changes(pair: tuple[Issue, Access] = Depends(get_issue_access),
                       db: AsyncSession = Depends(get_session)):
    issue, _ = pair
    rows = (await db.execute(select(TicketFileChange).where(TicketFileChange.issue_id == issue.id)
                             .order_by(TicketFileChange.path))).scalars().all()
    return [{"id": f.id, "path": f.path, "status": f.status, "additions": f.additions,
             "deletions": f.deletions} for f in rows]


@router.get("/issues/{key}/diff")
async def issue_diff(pair: tuple[Issue, Access] = Depends(get_issue_access),
                     db: AsyncSession = Depends(get_session)):
    """The complete git diff of the ticket branch (for the review)."""
    from urllib.parse import urlsplit

    from ..models.project import Project
    from ..worker import gitops
    from ..worker.secrets import resolve_git_token
    issue, _ = pair
    if not issue.branch_name:
        return {"diff": ""}
    project = await db.get(Project, issue.project_id)
    host = urlsplit(project.github_repo).hostname or ""
    owner = issue.assigned_by_user_id or issue.reporter_id or project.lead_user_id
    token = await resolve_git_token(db, project.git_token_enc, owner, host) or ""
    ctx = gitops.GitCtx(
        workdir=gitops.project_workdir(project.key), branch=issue.branch_name,
        remote=project.github_repo, token=token,
        worktree=gitops.worktree_path(project.key, issue.key),
        base_commit=issue.git_base_sha, main=project.merge_target or "main", enabled=True)
    return {"diff": await gitops.diff_text(ctx, max_chars=80000)}


@router.get("/issues/{key}/attachments")
async def list_attachments(pair: tuple[Issue, Access] = Depends(get_issue_access),
                           db: AsyncSession = Depends(get_session)):
    issue, _ = pair
    rows = (await db.execute(select(Attachment).where(Attachment.issue_id == issue.id)
                             .order_by(Attachment.id))).scalars().all()
    return [{"id": a.id, "filename": a.filename, "mime_type": a.mime_type, "size": a.size,
             "created_at": a.created_at} for a in rows]


@router.post("/issues/{key}/attachments", status_code=201)
async def upload_attachment(file: UploadFile, pair: tuple[Issue, Access] = Depends(get_issue_access),
                            db: AsyncSession = Depends(get_session)):
    issue, access = pair
    if not access.has_role(ProjectRole.member):
        raise Error(status.HTTP_403_FORBIDDEN, "err.write_rights_required",
                     "Write rights are required")
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.file_too_large_mb",
                     "File too large (>20MB)")
    a = Attachment(issue_id=issue.id, uploader_id=access.user.id, filename=file.filename or "datei",
                   mime_type=file.content_type or "application/octet-stream", size=len(raw), data=raw)
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return {"id": a.id, "filename": a.filename, "size": a.size}


async def _attachment_access(aid: int, user: User, db: AsyncSession) -> Attachment:
    """The attachment is only for members of the associated project (respectively admins)."""
    a = await db.get(Attachment, aid)
    if a is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.attachment_not_found", "Attachment not found")
    issue = await db.get(Issue, a.issue_id)
    project = await db.get(Project, issue.project_id) if issue else None
    if project is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.attachment_not_found", "Attachment not found")
    access = await build_access(project, user, db)
    if not access.has_role(ProjectRole.viewer):
        raise Error(status.HTTP_403_FORBIDDEN, "err.no_access_project",
                     "No access to this project")
    return a


@router.get("/attachments/{aid}")
async def download_attachment(aid: int, user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)):
    a = await _attachment_access(aid, user, db)
    return Response(content=a.data, media_type=a.mime_type,
                    headers={"Content-Disposition": f'inline; filename="{a.filename}"'})


@router.delete("/attachments/{aid}", status_code=204)
async def delete_attachment(aid: int, user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_session)):
    a = await _attachment_access(aid, user, db)
    if a.uploader_id != user.id:
        issue = await db.get(Issue, a.issue_id)
        project = await db.get(Project, issue.project_id)
        access = await build_access(project, user, db)
        if not access.has_role(ProjectRole.maintainer):
            raise Error(status.HTTP_403_FORBIDDEN, "err.only_uploader_maintainer_may_delete",
                         "Only the uploader or a maintainer may delete")
    await db.delete(a)
    await db.commit()
