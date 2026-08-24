"""Bug reports from outside: taking them in, looking at them, turning them into a ticket.

A report is NOT a ticket. It comes from somebody who ran into something, in their words,
often without the faintest idea which project takes care of it, and most of them never
become work: three reports of the same thing, one misunderstanding, one that turns out to be
the radio and not the program. Filing all of that straight onto a board buries the board.

So a report lands here first and stays a report until a person decides otherwise. That
decision is `to_ticket()`, and it is the only place where a report grows into work.

The report itself is an artifact of the type `bug` (`backing='generic'`), which means the
register already answers every question about it: which fields it carries, which states it
knows, what the interface shows. Nothing here re-invents that.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets as secrets_mod

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import hash_password, verify_password
from ..models.artifact import Artifact, ArtifactType
from ..models.bugs import BugSource
from . import artifact_fields as fields_svc
from . import artifacts as artifacts_svc

log = logging.getLogger("bugs")

TYPE_KEY = "bug"

# What kind of report it is. The words come from gameproj, which has had this since its
# migration 034 and whose players are the largest group of reporters: bug, feature, question.
# A shared vocabulary is worth more than a nicer one of our own, because the reports of both
# programs end up in the same list.
KINDS: list[tuple[str, str, str, bool]] = [
    ("bug", "Something is broken", "", False),
    ("feature", "A wish", "", False),
    ("question", "A question", "", False),
]

# (value, label, category, waits for a human)
STATUS: list[tuple[str, str, str, bool]] = [
    ("new", "New", "todo", True),
    ("seen", "Seen", "in_progress", False),
    ("in_progress", "Being worked on", "in_progress", False),
    ("ticket", "Became a ticket", "done", False),
    ("done", "Done", "done", False),
    ("rejected", "Rejected", "done", False),
    ("duplicate", "Duplicate", "done", False),
]

# gameproj has carried the same matter since its migration 023, in German words. The two lists
# have to meet somewhere, and the meeting point stands here rather than in gameproj: whoever
# connects the next program looks in one place for how its states are called.
UNIWAR_STATUS = {"offen": "new", "in_arbeit": "in_progress", "erledigt": "done",
                 "abgelehnt": "rejected"}
UNIWAR_KIND = {"bug": "bug", "feature": "feature", "frage": "question"}


# The fields of a report. `key` is what the reporting program sends, so renaming one breaks
# a program somebody else installed: the keys are locked (`builtin`), the labels are not.
FIELDS: list[dict] = [
    {"key": "status", "label": "State", "kind": "select", "options": STATUS},
    {"key": "kind", "label": "Kind", "kind": "select", "options": KINDS},
    {"key": "app", "label": "Program", "kind": "text"},
    {"key": "version", "label": "Version", "kind": "text"},
    {"key": "contact", "label": "Reporter", "kind": "text"},
    {"key": "environment", "label": "Environment", "kind": "text"},
    {"key": "details", "label": "What happened", "kind": "text"},
    {"key": "technical", "label": "Technical attachment", "kind": "text"},
    {"key": "ticket", "label": "Ticket", "kind": "text"},
    # How the reporting program recognises its own user again. Not a name: gameproj renames
    # players, and a report must stay theirs.
    {"key": "reporter_ref", "label": "Reporter reference", "kind": "text"},
    # Where a taken over report came from ("gameproj:17"). Keeps a second import from writing
    # everything a second time, and answers "was that not the one from back then?".
    {"key": "foreign_ref", "label": "Taken over from", "kind": "text"},
]

# What a report may carry along, and how much of it. A log that somebody pastes in whole is
# the useful part of these reports, so the attachment gets room; the rest stays short enough
# that no form turns into a storage bin.
LIMITS = {"title": 300, "details": 20_000, "technical": 200_000,
          "contact": 200, "version": 60, "environment": 500}


async def ensure_type(db: AsyncSession) -> ArtifactType:
    """Register the type `bug` including its fields. Idempotent, like the built-in types.

    Existing entries keep their label: whoever renamed "Reporter" into "Rufzeichen" in the
    administration means it that way.
    """
    kind = (await db.execute(select(ArtifactType).where(ArtifactType.key == TYPE_KEY))
            ).scalar_one_or_none()
    if kind is None:
        kind = ArtifactType(
            key=TYPE_KEY, name="Report", plural="Reports", icon="🐞", color="#e5615e",
            backing="generic", builtin=True,
            description="What a user ran into, wishes for or wants to know, in their own "
                        "words. Becomes a ticket only when somebody decides so.",
        )
        db.add(kind)
        await db.flush()

    there = {f.key: f for f in await fields_svc.fields_of(db, kind.id, only_active=False)}
    from ..models.artifact import ArtifactField, ArtifactFieldOption
    for order, spec in enumerate(FIELDS):
        field = there.get(spec["key"])
        if field is None:
            field = ArtifactField(type_id=kind.id, key=spec["key"], label=spec["label"],
                                  kind=spec["kind"], order=order, builtin=True)
            db.add(field)
            await db.flush()
        for i, (value, label, category, waits) in enumerate(spec.get("options", [])):
            have = {o.value for o in await fields_svc.options_of(db, field.id, only_active=False)}
            if value not in have:
                db.add(ArtifactFieldOption(field_id=field.id, value=value, label=label,
                                           order=i, category=category, waiting=waits))
    await db.commit()
    return kind


# ── The reporting programs ───────────────────────────────────────────────────

def new_token() -> str:
    """A token for a program. Shown once, stored as a hash."""
    return secrets_mod.token_urlsafe(32)


async def source_for_token(db: AsyncSession, token: str) -> BugSource | None:
    """Which program does this token belong to?

    Every enabled source is tried, because the token says nothing about its owner. That is a
    handful of hash comparisons: there are as many sources as there are programs of one's
    own, not as many as there are users.
    """
    if not token:
        return None
    rows = (await db.execute(select(BugSource).where(BugSource.enabled.is_(True)))).scalars()
    for source in rows:
        if source.token_hash and verify_password(token, source.token_hash):
            return source
    return None


def set_token(source: BugSource, token: str) -> None:
    source.token_hash = hash_password(token)
    source.token_hint = token[-6:]


async def within_limit(db: AsyncSession, source: BugSource, kind_id: int) -> bool:
    """Has this program stayed under its hourly ceiling?

    A form open to everybody is open to everybody, including whoever finds it funny to send
    a thousand of them. The ceiling is per program so that one chatty app cannot silence the
    others.
    """
    if source.hourly_limit <= 0:
        return True
    since = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=1)
    count = (await db.execute(
        select(func.count()).select_from(Artifact)
        .where(Artifact.type_id == kind_id, Artifact.created_at >= since)
        .where(Artifact.id.in_(_ids_of_source(source.key))))).scalar() or 0
    return count < source.hourly_limit


def _ids_of_source(key: str):
    """Artifact ids whose field `app` carries this program."""
    from ..models.artifact import ArtifactField, ArtifactValue
    return (select(ArtifactValue.artifact_id)
            .join(ArtifactField, ArtifactField.id == ArtifactValue.field_id)
            .where(ArtifactField.key == "app", ArtifactValue.value_text == key))


# ── Taking a report in ───────────────────────────────────────────────────────

async def create_report(db: AsyncSession, source: BugSource, payload: dict) -> Artifact:
    """Turn what a program sent into a report. The sender decides nothing but the content.

    State, program and project come from the source, not from the payload: whoever holds the
    token may say what happened, not where it lands or how urgent it is.
    """
    kind = await ensure_type(db)
    title = _short(payload.get("title"), LIMITS["title"]) or "Bug report without a title"
    artifact = Artifact(type_id=kind.id, project_id=source.project_id, title=title,
                        status_key="new")
    db.add(artifact)
    await db.flush()

    art = str(payload.get("kind") or "bug")
    if art not in {value for value, *_ in KINDS}:
        art = "bug"          # an unknown kind is a report all the same, not a reason to refuse
    values = {
        "status": "new",
        "kind": art,
        "app": source.key,
        "version": _short(payload.get("version"), LIMITS["version"]),
        "contact": _short(payload.get("contact"), LIMITS["contact"]),
        "environment": _short(payload.get("environment"), LIMITS["environment"]),
        "details": _short(payload.get("details"), LIMITS["details"]),
        "reporter_ref": _short(payload.get("external_ref"), 120),
        "technical": _short(payload.get("technical"), LIMITS["technical"]),
    }
    fields = {f.key: f for f in await fields_svc.fields_of(db, kind.id, only_active=False)}
    for key, value in values.items():
        field = fields.get(key)
        if field is not None and value:
            await fields_svc.set_values(db, artifact.id, field, [value])
    await db.commit()
    await db.refresh(artifact)
    log.info("bug report %s from %s: %s", artifact.id, source.key, title)
    return artifact


def _short(value, limit: int) -> str:
    """Cut an over-long value instead of refusing the report.

    A report that gets thrown away because the log was too big helps nobody: the first
    hundred lines of it usually carry the answer anyway.
    """
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[cut off]"


# ── From a report to work ────────────────────────────────────────────────────

async def to_ticket(db: AsyncSession, artifact: Artifact, project_id: int, user,
                    *, summary: str = "", description: str = ""):
    """Create a ticket out of a report and note on the report where it went.

    The description gets the whole report appended, attachment included. Whoever picks the
    ticket up later should not have to go looking for the report it came from, and an agent
    working on the ticket reads only what stands in it.
    """
    from .issues import new_issue

    body = description.strip() or await report_text(db, artifact)
    issue = await new_issue(db, project_id=project_id, summary=summary or artifact.title,
                            description=body, reporter_id=user.id,
                            source=f"bug:{artifact.id}")
    fields = {f.key: f for f in await fields_svc.fields_of(db, artifact.type_id,
                                                           only_active=False)}
    if fields.get("ticket") is not None:
        await fields_svc.set_values(db, artifact.id, fields["ticket"], [issue.key])
    if fields.get("status") is not None:
        await fields_svc.set_values(db, artifact.id, fields["status"], ["ticket"])
    artifact.status_key = "ticket"
    artifact.closed_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    return issue


async def report_text(db: AsyncSession, artifact: Artifact) -> str:
    """The report as one readable text, for the ticket description."""
    values = await fields_svc.values_of(db, artifact.id)

    def one(key: str) -> str:
        entries = values.get(key) or []
        return str(entries[0]) if entries else ""

    parts = [one("details") or artifact.title]
    facts = [(label, one(key)) for key, label in
             (("app", "Program"), ("version", "Version"), ("contact", "Reporter"),
              ("environment", "Environment"))]
    known = [f"- {label}: {value}" for label, value in facts if value]
    if known:
        parts.append("\n".join(["", "**Reported**", *known]))
    attachment = one("technical")
    if attachment:
        parts.append(f"\n**Attachment**\n\n```\n{attachment}\n```")
    return "\n".join(parts)


# ── The conversation ─────────────────────────────────────────────────────────

async def posts_of(db: AsyncSession, artifact_id: int, *, with_internal: bool):
    """The conversation, oldest first. `with_internal=False` is the view of the reporter.

    The flag is a parameter and not a filter the caller adds afterwards, because forgetting
    a `where` is quiet: the note among ourselves would simply be in the answer, and nobody
    would notice until the reporter quotes it back.
    """
    from ..models.bugs import ReportPost
    q = select(ReportPost).where(ReportPost.artifact_id == artifact_id)
    if not with_internal:
        q = q.where(ReportPost.internal.is_(False))
    return list((await db.execute(q.order_by(ReportPost.id))).scalars().all())


async def add_post(db: AsyncSession, artifact, body: str, *, author=None,
                   author_label: str = "", external_ref: str = "", internal: bool = False):
    """Say something about a report. From here or from over there."""
    from ..models.bugs import ReportPost
    post = ReportPost(
        artifact_id=artifact.id, body=body.strip()[:LIMITS["details"]],
        author_id=getattr(author, "id", None),
        author_label=author_label or getattr(author, "display_name", "") or
        getattr(author, "username", ""),
        external_ref=external_ref, internal=internal,
    )
    db.add(post)
    # A report somebody is talking about is no longer untouched. Only from "new" onwards:
    # whoever set it to rejected has decided, and an answer does not undo that.
    if artifact.status_key in ("", "new") and not internal:
        artifact.status_key = "seen"
        fields = {f.key: f for f in await fields_svc.fields_of(db, artifact.type_id,
                                                              only_active=False)}
        if fields.get("status") is not None:
            await fields_svc.set_values(db, artifact.id, fields["status"], ["seen"])
    await db.commit()
    await db.refresh(post)
    return post


async def add_image(db: AsyncSession, post, filename: str, mime_type: str, data: bytes,
                    *, artifact=None):
    """A picture for one answer, or - with `artifact` instead of `post` - for the report."""
    from ..models.bugs import ReportImage
    image = ReportImage(post_id=getattr(post, "id", None),
                        artifact_id=getattr(artifact, "id", None), filename=filename[:300],
                        mime_type=mime_type or "application/octet-stream",
                        size=len(data), data=data)
    db.add(image)
    await db.commit()
    await db.refresh(image)
    return image


async def images_of(db: AsyncSession, post_ids: list[int]) -> dict[int, list]:
    from ..models.bugs import ReportImage
    if not post_ids:
        return {}
    rows = (await db.execute(select(ReportImage.id, ReportImage.post_id, ReportImage.filename,
                                    ReportImage.mime_type, ReportImage.size)
                             .where(ReportImage.post_id.in_(post_ids))
                             .order_by(ReportImage.id))).all()
    out: dict[int, list] = {}
    for image_id, post_id, filename, mime, size in rows:
        out.setdefault(post_id, []).append(
            {"id": image_id, "filename": filename, "mime_type": mime, "size": size})
    return out


async def images_of_report(db: AsyncSession, artifact_id: int) -> list[dict]:
    """The pictures that came with the report itself."""
    from ..models.bugs import ReportImage
    rows = (await db.execute(select(ReportImage.id, ReportImage.filename, ReportImage.mime_type,
                                    ReportImage.size)
                             .where(ReportImage.artifact_id == artifact_id)
                             .order_by(ReportImage.id))).all()
    return [{"id": i, "filename": f, "mime_type": m, "size": s} for i, f, m, s in rows]


async def reports_of_external(db: AsyncSession, source: BugSource, external_ref: str):
    """The reports of one user of a reporting program, newest first.

    This is what the program shows its own people, so it must not hand out anything of
    anybody else: the reference of the user is required, an empty one returns nothing rather
    than everything.
    """
    if not external_ref:
        return []
    kind = await ensure_type(db)
    from ..models.artifact import ArtifactField, ArtifactValue
    mine = (select(ArtifactValue.artifact_id)
            .join(ArtifactField, ArtifactField.id == ArtifactValue.field_id)
            .where(ArtifactField.key == "reporter_ref", ArtifactValue.value_text == external_ref))
    rows = (await db.execute(
        select(Artifact).where(Artifact.type_id == kind.id, Artifact.id.in_(mine))
        .where(Artifact.id.in_(_ids_of_source(source.key)))
        .order_by(Artifact.id.desc()))).scalars().all()
    return list(rows)


# ── Telling the program that something happened ──────────────────────────────

async def notify_source(db: AsyncSession, artifact, *, event: str, post=None) -> None:
    """Say to the reporting program that one of its reports moved.

    Fire and forget: a program that is down or slow must not make answering here fail. What
    it gets is a pointer, not the content - it fetches the thread itself with its token, so
    the same rules apply as everywhere and an internal note cannot leak through a callback.
    """
    import httpx

    from ..models.bugs import BugSource
    values = await fields_svc.values_of(db, artifact.id)

    def one(key: str) -> str:
        entries = values.get(key) or []
        return str(entries[0]) if entries else ""

    source = (await db.execute(select(BugSource).where(BugSource.key == one("app")))
              ).scalar_one_or_none()
    if source is None or not source.callback_url or not source.enabled:
        return
    payload = {"event": event, "report_id": artifact.id, "external_ref": one("reporter_ref"),
               "status": artifact.status_key or "new", "title": artifact.title,
               "post_id": getattr(post, "id", None)}
    try:
        async with httpx.AsyncClient(timeout=8) as browser:
            # No secret travels along, and none is needed: the call is a doorbell, not a
            # message. Whoever forges it makes the program fetch a thread it is allowed to
            # fetch anyway, and it fetches with its own token.
            await browser.post(source.callback_url, json=payload,
                               headers={"X-Bug-Source": source.key})
    except httpx.HTTPError as exc:
        log.warning("callback to %s failed: %s", source.key, exc)
