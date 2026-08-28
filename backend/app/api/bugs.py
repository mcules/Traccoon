"""Bug reports: one door for programs, one for the people who look at them.

`POST /bugs/report` is the only endpoint in this house that a stranger reaches. It carries
no session, only the token of the reporting program, and it can do exactly one thing: file a
report. Everything that follows (reading, judging, turning it into work) needs a login like
the rest of the system.

Why an endpoint of its own instead of a webhook: a program that reports errors is the one
place where a bad answer must be readable. A webhook answers "accepted" and swallows the
rest into a flow, which is right for a machine trigger and wrong for a person who just
typed their callsign into a form and wants to know whether it arrived.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..core.error import Error
from ..db import get_session
from ..models.artifact import Artifact, ArtifactValue
from ..models.bugs import BugSource, ReportImage, ReportPost
from ..models.project import Project
from ..models.user import User
from ..schemas.bug import (
    AppPostIn, BugAppCount, BugKindCount, BugOut, BugReportAck, BugReportIn, BugSourceIn,
    BugSourceOut, BugStatusIn, BugToTicketIn, PostIn, PostOut, ThreadOut,
)
from ..services import artifact_fields as fields_svc
from ..services import bugs as svc
from .deps import get_current_user, require_admin

router = APIRouter(tags=["bugs"])

# A screenshot of a game or of a radio program is a few hundred kilobytes. The ceiling is
# there so that a report cannot become a storage bin, not to be generous.
IMAGE_LIMIT = 5_000_000


# ── The door for reporting programs ──────────────────────────────────────────

@router.post("/bugs/report", response_model=BugReportAck, status_code=201)
async def report(
    data: BugReportIn,
    request: Request,
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """File a report. Token in `X-Bug-Token` or as a bearer token."""
    token = x_bug_token or _bearer(request)
    source = await svc.source_for_token(db, token)
    if source is None:
        raise Error(status.HTTP_401_UNAUTHORIZED, "err.bug_token_unknown",
                    "This token belongs to no reporting program")
    kind = await svc.ensure_type(db)
    if not await svc.within_limit(db, source, kind.id):
        raise Error(status.HTTP_429_TOO_MANY_REQUESTS, "err.bug_limit_reached",
                    "{app} has reported too much in the last hour, please try again later",
                    app=source.name)
    artifact = await svc.create_report(db, source, data.model_dump())
    return BugReportAck(id=artifact.id, number=f"BUG-{artifact.id}")


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


# ── The door for the people who look at them ─────────────────────────────────

@router.get("/bugs", response_model=list[BugOut])
async def list_bugs(
    state: str = "",
    app: str = "",
    kind: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """All reports, newest first. `state=open` shows what nobody has judged yet."""
    # Not `kind`: that name belongs to the query parameter here, and the artifact type would
    # quietly shadow it. The filter below then compared a string against an ORM object and
    # every list came back empty.
    report_type = await svc.ensure_type(db)
    q = select(Artifact).where(Artifact.type_id == report_type.id).order_by(Artifact.id.desc())
    if state == "open":
        q = q.where(Artifact.status_key.in_(svc.OPEN_STATUS))
    elif state:
        q = q.where(Artifact.status_key == state)
    rows = list((await db.execute(q.limit(500))).scalars().all())
    out = [await _out(db, row) for row in rows]
    return [row for row in out
            if (not app or row.app == app) and (not kind or row.kind == kind)]


@router.get("/bugs/summary", response_model=list[BugKindCount])
async def summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """How many reports wait, per kind and per reporting program.

    For the box on the start page. "Open" means the same as in the list (`state=open`):
    new and seen, everything nobody has decided about.

    Counted in the database rather than by fetching the list and adding it up in the
    browser: the start page asks by itself every minute, and five hundred reports with
    their whole text behind four numbers would be the most expensive request of the page.

    A report whose kind nobody set comes back under the empty kind instead of falling out
    of the count — a figure that is smaller than the list below it is worse than an
    unnamed line.
    """
    report_type = await svc.ensure_type(db)
    fields = {f.key: f for f in await fields_svc.fields_of(db, report_type.id,
                                                           only_active=False)}
    # A missing field joins nothing: `-1` is no field id, so the outer join keeps the report
    # and leaves the value empty. Only happens on a half migrated register.
    def field_id(key: str) -> int:
        field = fields.get(key)
        return field.id if field is not None else -1

    kinds, apps = aliased(ArtifactValue), aliased(ArtifactValue)
    rows = (await db.execute(
        select(func.coalesce(kinds.value_text, ""), func.coalesce(apps.value_text, ""),
               func.count(Artifact.id))
        .select_from(Artifact)
        .outerjoin(kinds, and_(kinds.artifact_id == Artifact.id,
                               kinds.field_id == field_id("kind")))
        .outerjoin(apps, and_(apps.artifact_id == Artifact.id,
                              apps.field_id == field_id("app")))
        .where(Artifact.type_id == report_type.id,
               Artifact.status_key.in_(svc.OPEN_STATUS))
        .group_by(kinds.value_text, apps.value_text))).all()

    per_kind: dict[str, list[BugAppCount]] = {}
    for kind_value, app, count in rows:
        per_kind.setdefault(kind_value, []).append(BugAppCount(app=app, count=count))
    out = [BugKindCount(kind=kind_value,
                        count=sum(a.count for a in per_app),
                        apps=sorted(per_app, key=lambda a: (-a.count, a.app)))
           for kind_value, per_app in per_kind.items()]
    return sorted(out, key=lambda k: (-k.count, k.kind))


@router.get("/bugs/{bug_id}", response_model=BugOut)
async def get_bug(
    bug_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await _out(db, await _bug(db, bug_id))


@router.post("/bugs/{bug_id}/status", response_model=BugOut)
async def set_status(
    bug_id: int,
    data: BugStatusIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    artifact = await _bug(db, bug_id)
    known = {value for value, *_ in svc.STATUS}
    if data.status not in known:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.bug_status_unknown",
                    "'{state}' is not a state of a report", state=data.status)
    fields = {f.key: f for f in await fields_svc.fields_of(db, artifact.type_id,
                                                          only_active=False)}
    if fields.get("status") is not None:
        await fields_svc.set_values(db, artifact.id, fields["status"], [data.status])
    artifact.status_key = data.status
    await db.commit()
    await svc.notify_source(db, artifact, event="status")
    return await _out(db, artifact)


@router.post("/bugs/{bug_id}/ticket", response_model=BugOut)
async def to_ticket(
    bug_id: int,
    data: BugToTicketIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Make work out of a report. The ticket carries the whole report, attachment included."""
    from ..services.issues import NoTargetProject

    artifact = await _bug(db, bug_id)
    project = await db.get(Project, data.project_id)
    if project is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.project_not_found", "Project not found")
    from .deps import build_access
    from ..models.enums import GlobalRole, ProjectRole
    access = await build_access(project, user, db)
    if user.global_role != GlobalRole.admin and not access.has_role(ProjectRole.member):
        raise Error(status.HTTP_403_FORBIDDEN, "err.no_rights_on_project",
                    "No rights on the project {project}", project=project.key)
    try:
        await svc.to_ticket(db, artifact, data.project_id, user,
                            summary=data.summary, description=data.description)
    except NoTargetProject as exc:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.project_cannot_carry_tickets",
                    "The project {project} has no issue type, state or counter",
                    project=project.key) from exc
    return await _out(db, artifact)


async def _bug(db: AsyncSession, bug_id: int) -> Artifact:
    kind = await svc.ensure_type(db)
    artifact = await db.get(Artifact, bug_id)
    if artifact is None or artifact.type_id != kind.id:
        raise Error(status.HTTP_404_NOT_FOUND, "err.bug_not_found", "Report not found")
    return artifact


async def _out(db: AsyncSession, artifact: Artifact) -> BugOut:
    values = await fields_svc.values_of(db, artifact.id)

    def one(key: str) -> str:
        entries = values.get(key) or []
        return str(entries[0]) if entries else ""

    return BugOut(
        id=artifact.id, title=artifact.title, status=artifact.status_key or "new",
        kind=one("kind") or "bug",
        app=one("app"), version=one("version"), contact=one("contact"),
        environment=one("environment"), details=one("details"), technical=one("technical"),
        ticket=one("ticket"), project_id=artifact.project_id, created_at=artifact.created_at,
        images=await svc.images_of_report(db, artifact.id),
    )


# ── The reporting programs themselves ────────────────────────────────────────

@router.get("/bug-sources", response_model=list[BugSourceOut])
async def list_sources(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    rows = (await db.execute(select(BugSource).order_by(BugSource.key))).scalars().all()
    return [_source_out(row) for row in rows]


@router.post("/bug-sources", response_model=BugSourceOut, status_code=201)
async def create_source(
    data: BugSourceIn,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Register a program. The answer carries the token, once and never again."""
    there = (await db.execute(select(BugSource).where(BugSource.key == data.key))
             ).scalar_one_or_none()
    if there is not None:
        raise Error(status.HTTP_409_CONFLICT, "err.bug_source_exists",
                    "A program named '{key}' is already registered", key=data.key)
    source = BugSource(key=data.key, name=data.name, project_id=data.project_id,
                       hourly_limit=data.hourly_limit, description=data.description,
                       enabled=data.enabled, callback_url=data.callback_url)
    token = svc.new_token()
    svc.set_token(source, token)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    out = _source_out(source)
    out.token = token
    return out


@router.post("/bug-sources/{source_id}/token", response_model=BugSourceOut)
async def renew_token(
    source_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    source = await db.get(BugSource, source_id)
    if source is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.bug_source_not_found",
                    "Reporting program not found")
    token = svc.new_token()
    svc.set_token(source, token)
    await db.commit()
    out = _source_out(source)
    out.token = token
    return out


def _source_out(source: BugSource) -> BugSourceOut:
    return BugSourceOut(
        id=source.id, key=source.key, name=source.name, project_id=source.project_id,
        callback_url=source.callback_url,
        hourly_limit=source.hourly_limit, description=source.description,
        enabled=source.enabled, token_hint=source.token_hint,
    )


# ── The conversation, seen from here ─────────────────────────────────────────

@router.get("/bugs/{bug_id}/posts", response_model=list[PostOut])
async def read_posts(
    bug_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Everything that was said, internal notes included: this is the view with a login."""
    artifact = await _bug(db, bug_id)
    return await _posts_out(db, artifact, with_internal=True)


@router.post("/bugs/{bug_id}/posts", response_model=PostOut, status_code=201)
async def write_post(
    bug_id: int,
    data: PostIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    artifact = await _bug(db, bug_id)
    post = await svc.add_post(db, artifact, data.body, author=user, internal=data.internal)
    if not data.internal:
        # Only the public answer rings: an internal note is none of the reporter's business,
        # and even the fact that one exists is not.
        await svc.notify_source(db, artifact, event="answer", post=post)
    return _post_out(post, {}, mine=True)


@router.post("/bugs/posts/{post_id}/images", status_code=201)
async def attach_image(
    post_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    post = await db.get(ReportPost, post_id)
    if post is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.post_not_found", "Entry not found")
    data = await file.read()
    if len(data) > IMAGE_LIMIT:
        raise Error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "err.image_too_large",
                    "The picture is larger than {mb} MB", mb=IMAGE_LIMIT // 1_000_000)
    image = await svc.add_image(db, post, file.filename or "bild", file.content_type or "", data)
    return {"id": image.id, "filename": image.filename, "size": image.size}


@router.get("/bugs/images/{image_id}")
async def read_image(
    image_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await _image_response(db, image_id)


# ── The conversation, seen from the reporting program ────────────────────────

@router.get("/bugs/app/reports", response_model=list[ThreadOut])
async def app_reports(
    external_ref: str = "",
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """The reports of one user of the program, with their conversations.

    That the program has to name its user is the whole access check here: a token opens the
    reports of its own users and of nobody else's.
    """
    source = await _source(db, x_bug_token, request)
    rows = await svc.reports_of_external(db, source, external_ref.strip())
    return [await _thread_out(db, row) for row in rows]


@router.get("/bugs/app/open-count")
async def app_open_count(
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """How much of this program is still lying here unjudged.

    For the badge in the program's own header: whoever looks after it should see without
    asking that something is waiting, even though the answering happens over here.
    """
    source = await _source(db, x_bug_token, request)
    report_type = await svc.ensure_type(db)
    rows = (await db.execute(
        select(Artifact.status_key).where(Artifact.type_id == report_type.id)
        .where(Artifact.id.in_(svc._ids_of_source(source.key))))).scalars().all()
    return {"new": sum(1 for s in rows if s in ("", "new")),
            "in_progress": sum(1 for s in rows if s in ("seen", "in_progress", "ticket")),
            "total": len(rows)}


@router.get("/bugs/app/all", response_model=list[ThreadOut])
async def app_all_reports(
    state: str = "",
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """Every report of this program, for the view its own caretakers have.

    Without internal notes, like everywhere on this side: that a program may see all of its
    reports does not make it part of the house.
    """
    source = await _source(db, x_bug_token, request)
    report_type = await svc.ensure_type(db)
    q = (select(Artifact).where(Artifact.type_id == report_type.id)
         .where(Artifact.id.in_(svc._ids_of_source(source.key))).order_by(Artifact.id.desc()))
    if state:
        q = q.where(Artifact.status_key == state)
    rows = list((await db.execute(q.limit(500))).scalars().all())
    return [await _thread_out(db, row) for row in rows]


@router.post("/bugs/app/reports/{bug_id}/status", response_model=ThreadOut)
async def app_set_status(
    bug_id: int,
    data: BugStatusIn,
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """Set the state from the program. Two remote controls for one television: the state
    lives here, and whoever looks after the reports over there may reach it."""
    source = await _source(db, x_bug_token, request)
    artifact = await _app_bug(db, source, bug_id, "")
    known = {value for value, *_ in svc.STATUS}
    if data.status not in known:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.bug_status_unknown",
                    "'{state}' is not a state of a report", state=data.status)
    fields = {f.key: f for f in await fields_svc.fields_of(db, artifact.type_id,
                                                          only_active=False)}
    if fields.get("status") is not None:
        await fields_svc.set_values(db, artifact.id, fields["status"], [data.status])
    artifact.status_key = data.status
    await db.commit()
    return await _thread_out(db, artifact)


@router.get("/bugs/app/reports/{bug_id}", response_model=ThreadOut)
async def app_report(
    bug_id: int,
    external_ref: str = "",
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    source = await _source(db, x_bug_token, request)
    artifact = await _app_bug(db, source, bug_id, external_ref)
    return await _thread_out(db, artifact)


@router.post("/bugs/app/reports/{bug_id}/posts", response_model=PostOut, status_code=201)
async def app_write_post(
    bug_id: int,
    data: AppPostIn,
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """A reply written over there. The program vouches for its user, that is what the token is."""
    source = await _source(db, x_bug_token, request)
    artifact = await _app_bug(db, source, bug_id, data.external_ref)
    post = await svc.add_post(db, artifact, data.body, author_label=data.author or data.external_ref,
                              external_ref=data.external_ref)
    return _post_out(post, {}, mine=True)


@router.post("/bugs/app/posts/{post_id}/images", status_code=201)
async def app_attach_image(
    post_id: int,
    file: UploadFile = File(...),
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    source = await _source(db, x_bug_token, request)
    post = await db.get(ReportPost, post_id)
    if post is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.post_not_found", "Entry not found")
    artifact = await _app_bug(db, source, post.artifact_id, post.external_ref)
    data = await file.read()
    if len(data) > IMAGE_LIMIT:
        raise Error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "err.image_too_large",
                    "The picture is larger than {mb} MB", mb=IMAGE_LIMIT // 1_000_000)
    image = await svc.add_image(db, post, file.filename or "bild", file.content_type or "", data)
    return {"id": image.id, "filename": image.filename, "size": image.size, "report": artifact.id}


@router.post("/bugs/app/reports/{bug_id}/images", status_code=201)
async def app_attach_report_image(
    bug_id: int,
    external_ref: str = "",
    file: UploadFile = File(...),
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """A picture belonging to the report itself, not to an answer."""
    source = await _source(db, x_bug_token, request)
    artifact = await _app_bug(db, source, bug_id, external_ref)
    data = await file.read()
    if len(data) > IMAGE_LIMIT:
        raise Error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "err.image_too_large",
                    "The picture is larger than {mb} MB", mb=IMAGE_LIMIT // 1_000_000)
    image = await svc.add_image(db, None, file.filename or "bild", file.content_type or "",
                                data, artifact=artifact)
    return {"id": image.id, "filename": image.filename, "size": image.size}


@router.delete("/bugs/app/images/{image_id}", status_code=204)
async def app_delete_image(
    image_id: int,
    external_ref: str = "",
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    """Take a picture back. Only from one's own entry: whoever attached it may remove it."""
    source = await _source(db, x_bug_token, request)
    image = await db.get(ReportImage, image_id)
    if image is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.image_not_found", "Picture not found")
    if image.post_id is not None:
        post = await db.get(ReportPost, image.post_id)
        if post is None or post.internal or (external_ref and post.external_ref != external_ref):
            raise Error(status.HTTP_404_NOT_FOUND, "err.image_not_found", "Picture not found")
        await _app_bug(db, source, post.artifact_id, external_ref)
    else:
        await _app_bug(db, source, image.artifact_id or 0, external_ref)
    await db.delete(image)
    await db.commit()
    return None


@router.get("/bugs/app/images/{image_id}")
async def app_read_image(
    image_id: int,
    request: Request = None,  # type: ignore[assignment]
    x_bug_token: str = Header(default=""),
    db: AsyncSession = Depends(get_session),
):
    source = await _source(db, x_bug_token, request)
    image = await db.get(ReportImage, image_id)
    if image is not None:
        if image.post_id is not None:
            post = await db.get(ReportPost, image.post_id)
            # A picture hanging off an internal note is internal too.
            if post is None or post.internal:
                raise Error(status.HTTP_404_NOT_FOUND, "err.image_not_found",
                            "Picture not found")
            await _app_bug(db, source, post.artifact_id, "")
        else:
            await _app_bug(db, source, image.artifact_id or 0, "")
    return await _image_response(db, image_id)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _source(db: AsyncSession, header_token: str, request: Request | None) -> BugSource:
    token = header_token or (_bearer(request) if request is not None else "")
    source = await svc.source_for_token(db, token)
    if source is None:
        raise Error(status.HTTP_401_UNAUTHORIZED, "err.bug_token_unknown",
                    "This token belongs to no reporting program")
    return source


async def _app_bug(db: AsyncSession, source: BugSource, bug_id: int, external_ref: str):
    """A report the program may see: its own, and if a user is named, only theirs."""
    artifact = await _bug(db, bug_id)
    values = await fields_svc.values_of(db, artifact.id)

    def one(key: str) -> str:
        entries = values.get(key) or []
        return str(entries[0]) if entries else ""

    if one("app") != source.key or (external_ref and one("reporter_ref") != external_ref):
        # Deliberately the same answer as for a report that does not exist: whoever asks with
        # a foreign id should not learn from the answer that it was the right one.
        raise Error(status.HTTP_404_NOT_FOUND, "err.bug_not_found", "Report not found")
    return artifact


async def _posts_out(db: AsyncSession, artifact, *, with_internal: bool,
                     external_ref: str = "") -> list[PostOut]:
    posts = await svc.posts_of(db, artifact.id, with_internal=with_internal)
    images = await svc.images_of(db, [p.id for p in posts])
    return [_post_out(p, images, mine=bool(external_ref) and p.external_ref == external_ref)
            for p in posts]


def _post_out(post, images: dict, *, mine: bool = False) -> PostOut:
    return PostOut(id=post.id, body=post.body, author=post.author_label or "?",
                   internal=post.internal, team=not post.external_ref, mine=mine,
                   images=images.get(post.id, []), created_at=post.created_at)


async def _thread_out(db: AsyncSession, artifact) -> ThreadOut:
    bug = await _out(db, artifact)
    posts = await _posts_out(db, artifact, with_internal=False)
    # The last public entry counts, not the artifact's own timestamp: an internal note is
    # nothing that happened as far as the reporter is concerned.
    last = posts[-1].created_at if posts else artifact.created_at
    return ThreadOut(id=bug.id, title=bug.title, kind=bug.kind, status=bug.status,
                     details=bug.details, contact=bug.contact, images=bug.images,
                     created_at=bug.created_at, updated_at=last, posts=posts)


async def _image_response(db: AsyncSession, image_id: int):
    from fastapi.responses import Response
    image = await db.get(ReportImage, image_id)
    if image is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.image_not_found", "Picture not found")
    return Response(content=image.data, media_type=image.mime_type,
                    headers={"Content-Disposition": f'inline; filename="{image.filename}"'})
