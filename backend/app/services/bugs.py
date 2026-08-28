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

# What kind of report it is. The words come from the first program that reported here,
# which has had this since its
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

# What "open" means for a report: nobody has decided about it yet. Seen counts as open —
# somebody has looked, and looking is not deciding. Stands here because the list and the
# figure on the start page have to mean the same thing by it; two hand written tuples drift
# apart the first time a state is added.
OPEN_STATUS: tuple[str, ...] = ("new", "seen")

# That program has carried the same matter for as long, in German words. The two lists
# have to meet somewhere, and the meeting point stands here rather than over there: whoever
# connects the next program looks in one place for how its states are called.
FOREIGN_STATUS = {"offen": "new", "in_arbeit": "in_progress", "erledigt": "done",
                 "abgelehnt": "rejected"}
FOREIGN_KIND = {"bug": "bug", "feature": "feature", "frage": "question"}


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
    # How the reporting program recognises its own user again. Not a name: a program renames
    # players, and a report must stay theirs.
    {"key": "reporter_ref", "label": "Reporter reference", "kind": "text"},
    # Where a taken over report came from ("game:17"). Keeps a second import from writing
    # everything a second time, and answers "was that not the one from back then?".
    {"key": "foreign_ref", "label": "Taken over from", "kind": "text"},
    # ── The way by mail ─────────────────────────────────────────────────────
    # Where the reporter reads an answer. Deliberately not `contact`: that one carries
    # whatever the reporter typed (a callsign, a first name, sometimes an address), and
    # sending mail to a callsign is how one silently answers nobody.
    {"key": "reply_email", "label": "Reply address", "kind": "text"},
    # Which mailbox carries this conversation, and under which address. Held on the report and
    # not looked up from the project every time, because it decides where the reply lands: a
    # report answered from one address must not suddenly answer from another one when the
    # project is reconfigured six months later.
    {"key": "mail_account", "label": "Answering mailbox", "kind": "text"},
    {"key": "mail_from", "label": "Answering address", "kind": "text"},
    # The secret half of the `Message-ID` we send out. The reply carries it back in
    # `In-Reply-To`, and that is what makes it this report's reply and not one anybody could
    # write by knowing the number.
    {"key": "mail_key", "label": "Mail reference", "kind": "text"},
    # The `Message-ID` of the mail this report was made out of. Only reports that came in by
    # mail have one, and it is what a second mail of the same person refers back to before
    # anybody has answered — without it that mail would open a second report.
    {"key": "mail_ref", "label": "Mail of origin", "kind": "text"},
]

# What a report may carry along, and how much of it. A log that somebody pastes in whole is
# the useful part of these reports, so the attachment gets room; the rest stays short enough
# that no form turns into a storage bin.
LIMITS = {"title": 300, "details": 20_000, "technical": 200_000,
          "contact": 200, "version": 60, "environment": 500, "reply_email": 320}


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
    contact = _short(payload.get("contact"), LIMITS["contact"])
    values = {
        "status": "new",
        "kind": art,
        "app": source.key,
        "version": _short(payload.get("version"), LIMITS["version"]),
        "contact": contact,
        "environment": _short(payload.get("environment"), LIMITS["environment"]),
        "details": _short(payload.get("details"), LIMITS["details"]),
        "reporter_ref": _short(payload.get("external_ref"), 120),
        "technical": _short(payload.get("technical"), LIMITS["technical"]),
        # A program that knows the mail address of its user sends it along; where it does
        # not, `contact` is taken, but only when it really is an address. Most reporters
        # type a callsign or a first name in there.
        "reply_email": _address(payload.get("reply_email")) or _address(contact),
        # Which mailbox answers is decided by the project of the program, once, at intake —
        # see the field.
        **await _project_sender(db, source.project_id),
        "mail_key": mail_key(),
    }
    fields = {f.key: f for f in await fields_svc.fields_of(db, kind.id, only_active=False)}
    for key, value in values.items():
        field = fields.get(key)
        if field is not None and value:
            await fields_svc.set_values(db, artifact.id, field, [value])
    await db.commit()
    await db.refresh(artifact)
    log.info("bug report %s from %s: %s", artifact.id, source.key, title)

    # A report is an occasion, not only a row. Whoever wants to hear about it (a message on
    # the phone, a ticket right away, a note in the vault) hangs a flow on this event instead
    # of somebody having to remember to look at the list.
    #
    # After the commit on purpose: the flow starts at once and reads the report, so it has to
    # be there. And in a `try`, because a report must never be lost over a broken flow — the
    # sender is a stranger's program that gets one attempt.
    from .events import emit
    try:
        await emit(db, "bug.reported", project_id=source.project_id,
                   payload={"report": {"id": artifact.id, "title": title, "kind": art,
                                       "app": source.key, "program": source.name,
                                       "version": values["version"],
                                       "contact": values["contact"],
                                       "reply_email": values["reply_email"],
                                       "details": values["details"],
                                       "project_id": source.project_id}})
    except Exception:  # noqa: BLE001
        log.exception("bug report %s: the event could not be reported", artifact.id)
    return artifact


async def _project_sender(db: AsyncSession, project_id: int | None) -> dict:
    """Mailbox and address this project answers from, as report fields.

    Both or neither: an address without a mailbox sends nothing, and a mailbox without an
    address has nothing to put on the envelope.
    """
    if not project_id:
        return {"mail_account": "", "mail_from": ""}
    from ..models.project import Project
    project = await db.get(Project, project_id)
    if project is None or not project.mail_account_id or not project.reply_from:
        return {"mail_account": "", "mail_from": ""}
    return {"mail_account": str(project.mail_account_id),
            "mail_from": project.reply_from}


def mail_key() -> str:
    """The secret half of the reference a mail reply carries back.

    Short on purpose: it stands in a `Message-ID` that a reporter sees in their mail program,
    and it guards a conversation about a bug report, not a bank account. What it has to
    prevent is that knowing the report number is enough to write into a thread.
    """
    return secrets_mod.token_hex(6)


def _address(value) -> str:
    """`value` when it is a mail address, otherwise empty.

    The check is deliberately coarse (something, an @, something with a dot): whoever types
    an address in a way this refuses would have got no answer either way, and a stricter
    rule throws away more real addresses than fake ones.
    """
    text = str(value or "").strip()
    if text.count("@") != 1 or " " in text:
        return ""
    local, _, domain = text.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return text[:LIMITS["reply_email"]]


def _short(value, limit: int) -> str:
    """Cut an over-long value instead of refusing the report.

    A report that gets thrown away because the log was too big helps nobody: the first
    hundred lines of it usually carry the answer anyway.
    """
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[cut off]"


async def create_here(db: AsyncSession, user, *, title: str, kind: str = "question",
                      details: str = "", contact: str = "", reply_email: str = "",
                      account_id: int | None = None, mail_from: str = "",
                      project_id: int | None = None) -> Artifact:
    """A report started in Traccoon instead of arriving from somewhere.

    Not every conversation begins with somebody filling in a form. A mail that has to be
    written to a reporter, a question to a club member, a matter that is neither a ticket nor
    a mailbox thread — that is a report too, only the first sentence comes from this side.

    It is the same artifact as an incoming one, deliberately: everything that already exists
    around a report (the thread, the way to a ticket, the states) works on it without a
    second kind of thing.
    """
    art_type = await ensure_type(db)
    artifact = Artifact(type_id=art_type.id, project_id=project_id,
                        title=_short(title, LIMITS["title"]) or "Report without a title",
                        status_key="new")
    db.add(artifact)
    await db.flush()
    values = {
        "status": "new",
        "kind": kind if kind in {value for value, *_ in KINDS} else "question",
        "contact": _short(contact, LIMITS["contact"]),
        "details": _short(details, LIMITS["details"]),
        "reply_email": _address(reply_email) or _address(contact),
        # Named explicitly, otherwise the mailbox of the project. A conversation without
        # either stays inside the house — it is answered where it can be read.
        **({"mail_account": str(account_id), "mail_from": _address(mail_from)}
           if account_id and _address(mail_from)
           else await _project_sender(db, project_id)),
        "mail_key": mail_key(),
    }
    fields = {f.key: f for f in await fields_svc.fields_of(db, art_type.id, only_active=False)}
    for key, value in values.items():
        field = fields.get(key)
        if field is not None and value:
            await fields_svc.set_values(db, artifact.id, field, [value])
    await db.commit()
    await db.refresh(artifact)
    log.info("report %s opened by %s: %s", artifact.id,
             getattr(user, "username", "?"), artifact.title)
    return artifact


async def set_field(db: AsyncSession, artifact, key: str, value: str) -> None:
    """Write one field of a report. For the handful the interface may correct later."""
    fields = {f.key: f for f in await fields_svc.fields_of(db, artifact.type_id,
                                                          only_active=False)}
    field = fields.get(key)
    if field is None:
        return
    await fields_svc.set_values(db, artifact.id, field, [value] if value else [])
    await db.commit()


async def values_of_report(db: AsyncSession, artifact_id: int) -> dict[str, str]:
    """All fields of a report as plain text, one value each. For whoever needs several."""
    values = await fields_svc.values_of(db, artifact_id)
    return {key: str(entries[0]) if entries else ""
            for key, entries in values.items()}


async def value_of(db: AsyncSession, artifact_id: int, key: str) -> str:
    """One field of a report as text. Empty when it is not set."""
    values = await fields_svc.values_of(db, artifact_id)
    entries = values.get(key) or []
    return str(entries[0]) if entries else ""


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
                   author_label: str = "", external_ref: str = "", internal: bool = False,
                   via: str = "", message_id: str = ""):
    """Say something about a report. From here, from over there, or by mail."""
    from ..models.bugs import ReportPost
    post = ReportPost(
        artifact_id=artifact.id, body=body.strip()[:LIMITS["details"]],
        author_id=getattr(author, "id", None),
        author_label=author_label or getattr(author, "display_name", "") or
        getattr(author, "username", ""),
        external_ref=external_ref, internal=internal,
        # Without a word for it: an entry with a reference to a user over there came out of
        # the program, everything else was written here. That is what the migration made of
        # the existing rows, and it stays true for callers that do not say.
        via=via or ("app" if external_ref else "web"),
        message_id=message_id[:400],
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


# ── Gelesen und ungelesen ────────────────────────────────────────────────────
#
# Ungelesen ist genau eines: ein Eintrag der Gegenseite, den diese Person noch nicht gesehen
# hat. Was wir selbst geschrieben haben, ist nie ungelesen (wir waren dabei), und eine interne
# Notiz auch nicht — sie steht unter uns und ist keine Antwort, auf die jemand wartet.
#
# Warum das überhaupt gezählt wird: eine Meldung ist ein Gespräch, und ein Gespräch, dessen
# letzter Satz ungehört bleibt, ist abgebrochen. Der Melder wartet dann auf jemanden, der gar
# nicht weiß, dass er dran ist.

FROM_OUTSIDE = ("app", "mail")


def _from_outside(query):
    from ..models.bugs import ReportPost
    return query.where(ReportPost.internal.is_(False), ReportPost.via.in_(FROM_OUTSIDE))


async def unread_of(db: AsyncSession, user_id: int, artifact_ids: list[int]) -> dict[int, int]:
    """Wie viele Einträge der Gegenseite diese Person je Meldung noch nicht gesehen hat.

    In EINER Abfrage für die ganze Liste: die Übersicht zeigt bis zu fünfhundert Meldungen,
    und eine Abfrage je Zeile wäre der teuerste Teil der Seite.
    """
    from ..models.bugs import ReportPost, ReportRead
    if not artifact_ids:
        return {}
    marks = dict((await db.execute(
        select(ReportRead.artifact_id, ReportRead.last_post_id)
        .where(ReportRead.user_id == user_id,
               ReportRead.artifact_id.in_(artifact_ids)))).all())
    rows = (await db.execute(_from_outside(
        select(ReportPost.artifact_id, ReportPost.id)
        .where(ReportPost.artifact_id.in_(artifact_ids))))).all()
    out: dict[int, int] = {}
    for artifact_id, post_id in rows:
        if post_id > marks.get(artifact_id, 0):
            out[artifact_id] = out.get(artifact_id, 0) + 1
    return out


async def unread_total(db: AsyncSession, user_id: int) -> dict[str, int]:
    """Wie viel für diese Person offen ist: Antworten und Meldungen, in denen sie stehen."""
    kind = await ensure_type(db)
    ids = list((await db.execute(
        select(Artifact.id).where(Artifact.type_id == kind.id))).scalars().all())
    per_report = await unread_of(db, user_id, ids)
    return {"posts": sum(per_report.values()), "reports": len(per_report)}


# Wann eine Meldung nicht mehr auf uns wartet. `ticket` ist kein Abschluss des Gesprächs,
# aber die Arbeit hat einen anderen Ort bekommen; wer danach noch etwas schreibt, taucht über
# "ungelesen" auf.
SETTLED = ("done", "rejected", "duplicate", "ticket")


async def unanswered(db: AsyncSession) -> list[int]:
    """Meldungen, in denen der letzte Satz nicht von uns ist.

    Das ist die Frage "wer wartet auf ein Wort von uns": eine frische Meldung, auf die noch
    niemand geantwortet hat, und eine, in der der Melder zuletzt geschrieben hat. Interne
    Notizen zählen nicht als Antwort — sie stehen unter uns, der Melder sieht sie nie.

    Nicht personenbezogen, anders als "ungelesen": ob jemand wartet, hängt nicht daran, wer
    von uns gerade hinsieht.
    """
    from ..models.bugs import ReportPost
    kind = await ensure_type(db)
    offen = list((await db.execute(
        select(Artifact.id).where(Artifact.type_id == kind.id,
                                  Artifact.status_key.not_in(SETTLED)))).scalars().all())
    if not offen:
        return []
    # Der jüngste öffentliche Beitrag je Meldung. Steht dort nichts, hat nie jemand
    # geantwortet — dann ist die Meldung selbst das Letzte, und die kam von drüben.
    rows = (await db.execute(
        select(ReportPost.artifact_id, ReportPost.via)
        .where(ReportPost.artifact_id.in_(offen), ReportPost.internal.is_(False))
        .order_by(ReportPost.artifact_id, ReportPost.id))).all()
    letzter: dict[int, str] = {}
    for artifact_id, via in rows:
        letzter[artifact_id] = via or "web"
    return [one for one in offen if letzter.get(one, "mail") in FROM_OUTSIDE]


async def waiting(db: AsyncSession, user_id: int) -> dict[str, int]:
    """Was wartet: ungelesen (für diese Person) und unbeantwortet (für alle).

    Zwei verschiedene Fragen, deshalb zwei Zahlen. Ungelesen heißt "ich kenne es noch nicht",
    unbeantwortet heißt "der Melder hat noch nichts von uns gehört" — und beides kann ohne
    das andere zutreffen: eine gelesene Meldung, auf die niemand geantwortet hat, ist der
    häufigste Fall von allen.
    """
    total = await unread_total(db, user_id)
    return {"unread_posts": total["posts"], "unread_reports": total["reports"],
            "unanswered": len(await unanswered(db))}


async def mark_read(db: AsyncSession, user_id: int, artifact_id: int) -> None:
    """Diese Person hat die Unterhaltung gesehen — bis zum aktuell letzten Eintrag.

    Wird beim Lesen des Verlaufs gesetzt, nicht beim Antworten: gesehen hat man etwas, wenn
    man es aufgemacht hat, und wer liest und nichts sagt, hat es trotzdem gelesen.
    """
    from ..models.bugs import ReportPost, ReportRead
    newest = (await db.execute(
        select(func.max(ReportPost.id)).where(ReportPost.artifact_id == artifact_id))
    ).scalar() or 0
    mark = (await db.execute(select(ReportRead).where(
        ReportRead.user_id == user_id, ReportRead.artifact_id == artifact_id))
    ).scalar_one_or_none()
    if mark is None:
        db.add(ReportRead(user_id=user_id, artifact_id=artifact_id, last_post_id=newest))
    elif mark.last_post_id < newest:
        mark.last_post_id = newest
        mark.seen_at = dt.datetime.now(tz=dt.timezone.utc)
    else:
        return
    await db.commit()


async def who_cares(db: AsyncSession, artifact) -> int | None:
    """Wer erfahren soll, dass sich in dieser Meldung etwas getan hat.

    Die Leitung des Projekts, zu dem die Meldung gehört — sie kümmert sich um dessen
    Meldungen. Sonst der Besitzer des Postfachs, aus dem geantwortet wird: geht die
    Unterhaltung über seine Adresse, ist sie seine. Findet sich beides nicht, geht die
    Meldung an niemanden, und das ist besser als an irgendwen.
    """
    from ..models.project import Project
    project = await db.get(Project, artifact.project_id) if artifact.project_id else None
    if project is not None and project.lead_user_id:
        return project.lead_user_id
    from . import report_mail
    pair = await report_mail.sender_of(db, artifact)
    return pair[0].owner_user_id if pair is not None else None


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
