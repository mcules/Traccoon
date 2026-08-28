"""The way by mail of a report: answering, and taking the answer back in.

A report used to have exactly one way back, the program it came from. That works as long as
the reporter sits in front of that program — a player in a game, a learner in a course. It
does not work for the other half: somebody who wrote once, out of a form, and will not open
that page again. Their answer waits in Traccoon and is never read.

So a report gets a second channel, and both are used when both are known: the program hears
through its callback (`bugs.notify_source`), the reporter reads a mail. What comes back by
mail becomes an entry in the same thread — the conversation is one conversation, whichever
door a sentence came through.

How a reply finds its way home. Three ways, in this order:

1. The `Message-ID` we sent under. It reads `<bug42.a1b2c3d4e5f6.7@example.org>`: the number
   of the report, its secret and the entry. Every mail program carries it back in
   `In-Reply-To`, so this is the way that works — no address trickery, nothing the reporter
   has to leave standing in the subject.
2. A reply address with the same reference in it (`...+bug42.a1b2c3d4e5f6@example.org`).
   Not sent by us (a mail server has to be set up for it, and one that is not bounces the
   answer), but recognised: whoever configures it gets the shorter way for free.
3. The tag in the subject, `[BUG-42]`, together with the sender being the address of the
   report. Both halves are needed: the tag alone would let anybody who knows a number write
   into a stranger's conversation.

The secret is why the number in the subject is not enough on its own. It is short — this
guards a bug report, not a bank account — and its whole job is that knowing "there is a
report 42" does not let one write in it.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.artifact import Artifact
from ..models.bugs import BugSource, ReportPost
from ..models.mail import MailAccount, MailIdentity
from . import bugs as bugs_svc
from .i18n import tr

log = logging.getLogger("report_mail")

# `bug<number>.<secret>` — in a Message-ID (followed by `.` and the entry) and in a reply
# address (followed by `@`). One expression for both, because it is one reference.
REFERENCE = re.compile(r"bug(\d+)\.([0-9a-f]{6,32})[.@]")
SUBJECT_TAG = re.compile(r"\[BUG-(\d+)\]")

# Headers that say to which of our addresses a mail was actually delivered. `To` can be
# anything (a mailing list, a Bcc), these are written by the delivering server.
DELIVERY_HEADERS = ("Delivered-To", "Envelope-To", "X-Original-To")

# Where the quoted mail begins. Everything from here on is what we wrote ourselves, and
# putting it into the thread a second time turns three sentences into three pages.
QUOTE_MARKS = (
    re.compile(r"^\s*>"),
    re.compile(r"^\s*-{2,}\s*Original(-| )(Message|Nachricht)", re.I),
    re.compile(r"^\s*Am .{5,60} schrieb .{0,80}:\s*$"),
    re.compile(r"^\s*On .{5,60} wrote:\s*$"),
    re.compile(r"^\s*-- \s*$"),
)


# ── Which address answers ────────────────────────────────────────────────────

async def sender_of(db: AsyncSession, artifact: Artifact
                    ) -> tuple[MailAccount, MailIdentity] | None:
    """The mailbox this report answers from and the address on the envelope, or None.

    Held on the report (fields `mail_account` and `mail_from`) and only looked up from its
    project when it is not yet set — and then written down. A conversation must keep answering
    from the address it started with, even when the project is pointed at another one later:
    otherwise the reporter gets an answer from a stranger, and their reply goes to a mailbox
    that knows nothing about them.

    The address comes back as a `MailIdentity` that is deliberately NOT in the database: it is
    what `mailbox.build_message` reads (sender, display name, signature), and inventing a row
    for it would put a second, half-maintained list of addresses next to the real one. The
    login stays where it is maintained — on the account.
    """
    from ..models.project import Project

    account_id = await bugs_svc.value_of(db, artifact.id, "mail_account")
    address = await bugs_svc.value_of(db, artifact.id, "mail_from")
    name = ""
    if not account_id or not address:
        project = (await db.get(Project, artifact.project_id)
                   if artifact.project_id else None)
        if project is None or not project.mail_account_id or not project.reply_from:
            return None
        account_id, address, name = (str(project.mail_account_id), project.reply_from,
                                     project.reply_name)
        await bugs_svc.set_field(db, artifact, "mail_account", account_id)
        await bugs_svc.set_field(db, artifact, "mail_from", address)

    account = await db.get(MailAccount, int(account_id)) if account_id.isdigit() else None
    if account is None or not account.enabled or not account.smtp_host:
        return None
    if not name:
        # The name in front of the address, when the project has one for this mailbox.
        project = (await db.get(Project, artifact.project_id)
                   if artifact.project_id else None)
        name = project.reply_name if project is not None and project.reply_from == address else ""
    return account, MailIdentity(account_id=account.id, email=address, display_name=name)


async def reachable(db: AsyncSession, artifact: Artifact) -> str:
    """The address an answer would go to, empty when there is none.

    Two conditions, and both have to hold: somebody to write to, and a mailbox to write from.
    The interface shows this so that whoever types an answer knows beforehand whether it
    travels — an answer that only lands in the list is the failure this whole way exists
    against.
    """
    address = await bugs_svc.value_of(db, artifact.id, "reply_email")
    if not address:
        return ""
    return address if await sender_of(db, artifact) is not None else ""


# ── Answering ────────────────────────────────────────────────────────────────

def _message_id(artifact_id: int, key: str, post_id: int, domain: str) -> str:
    return f"<bug{artifact_id}.{key}.{post_id}@{domain or 'traccoon.invalid'}>"


async def _thread_reference(db: AsyncSession, artifact_id: int) -> str:
    """The Message-ID of the last mail in this conversation, ours or theirs.

    What makes the mail program of the reporter file our answer under the mail they wrote,
    instead of opening a second conversation about the same matter.
    """
    row = (await db.execute(
        select(ReportPost.message_id).where(ReportPost.artifact_id == artifact_id)
        .where(ReportPost.message_id != "").order_by(ReportPost.id.desc()).limit(1))
    ).scalar_one_or_none()
    # A report that came in as a mail and has not been talked about yet has no entry with a
    # Message-ID — the text of that mail is the report itself. Its reference stands on the
    # report, and the first answer has to hang off it.
    return str(row or "") or await bugs_svc.value_of(db, artifact_id, "mail_ref")


async def send_answer(db: AsyncSession, artifact: Artifact, post: ReportPost) -> bool:
    """Send one entry of the thread to the reporter. Returns whether it went out.

    Internal notes never come in here — they do not travel, and that is checked at the door
    they come through, not by hoping every caller remembers. Whoever calls this with one has
    made a mistake, so it refuses instead of quietly not sending.
    """
    if post.internal:
        raise ValueError("an internal note must not be sent")

    address = await bugs_svc.value_of(db, artifact.id, "reply_email")
    if not address:
        return False
    pair = await sender_of(db, artifact)
    if pair is None:
        return False
    account, identity = pair

    key = await bugs_svc.value_of(db, artifact.id, "mail_key")
    if not key:
        # Reports from before this way existed carry no secret. It is made up now, at the
        # first answer, which is the moment it starts being needed.
        key = bugs_svc.mail_key()
        await bugs_svc.set_field(db, artifact, "mail_key", key)

    domain = identity.email.rpartition("@")[2]
    message_id = _message_id(artifact.id, key, post.id, domain)
    earlier = await _thread_reference(db, artifact.id)
    tag = f"[BUG-{artifact.id}]"
    subject = f"{'Re: ' if earlier else ''}{tag} {artifact.title}"[:400]

    footer = await tr(db, "server.report.mail_footer", tag=tag)
    fields = {
        "to": [address],
        "subject": subject,
        "text": f"{post.body}\n\n{footer}",
        "in_reply_to": earlier,
        "message_id": message_id,
    }
    from . import mailbox
    try:
        await mailbox.send(account, identity, fields)
    except Exception:  # noqa: BLE001
        # The entry stands in the thread either way. A mailbox that is down must not swallow
        # an answer that was written — it is visible here, and in the app, and can be sent
        # again by writing again.
        log.exception("report %s: the answer to %s did not go out", artifact.id, address)
        return False

    post.message_id = message_id
    await db.commit()
    log.info("report %s: answer sent to %s", artifact.id, address)
    return True


# ── Taking an answer back in ─────────────────────────────────────────────────

def _header(payload: dict, name: str) -> str:
    """One header out of the watcher payload, as one string.

    A header can appear more than once (`Received`, and `References` on the way through some
    servers), in which case the payload holds a list. For searching a reference in it, the
    pieces joined together are exactly as good as the pieces apart.
    """
    raw = (payload.get("headers") or {}).get(name)
    if isinstance(raw, list):
        return " ".join(str(one) for one in raw)
    return str(raw or "")


def _addresses(payload: dict) -> list[str]:
    """Every address this mail was sent or delivered to, lower case."""
    out: list[str] = []
    for field in ("to", "cc"):
        for entry in payload.get(field) or []:
            if isinstance(entry, dict) and entry.get("addr"):
                out.append(str(entry["addr"]).lower())
    for name in DELIVERY_HEADERS:
        value = _header(payload, name).strip().strip("<>").lower()
        if value:
            out.append(value)
    return out


def _sender(payload: dict) -> tuple[str, str]:
    """Name and address of the sender, empty when the mail carries none."""
    for entry in payload.get("from") or []:
        if isinstance(entry, dict) and entry.get("addr"):
            return str(entry.get("name") or ""), str(entry["addr"]).lower()
    return "", ""


def body_of(payload: dict) -> str:
    """The text of the mail without the part that quotes our own answer back at us."""
    raw = str(payload.get("body_text") or payload.get("body_html_as_text") or "")
    lines: list[str] = []
    for line in raw.splitlines():
        if any(mark.match(line) for mark in QUOTE_MARKS):
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    # A reply that is nothing but quotation (somebody who wrote above their signature and
    # below nothing) still says something: rather the whole mail than an empty entry.
    return text or raw.strip()


async def _by_reference(db: AsyncSession, text: str) -> Artifact | None:
    """A report whose number AND secret stand in this text."""
    for number, key in REFERENCE.findall(text or ""):
        artifact = await _report(db, int(number))
        if artifact is None:
            continue
        if await bugs_svc.value_of(db, artifact.id, "mail_key") == key:
            return artifact
    return None


async def _by_known_id(db: AsyncSession, chain: str) -> Artifact | None:
    """A report to which one of the referred to Message-IDs already belongs.

    Needed for the mails we did not write: somebody who writes a second time before an
    answer went out refers to their own first mail, and that one carries no reference of
    ours. A foreign Message-ID is not guessed, it is recognised — so only ids we have on
    file count, from an entry or from the mail a report was made out of.
    """
    ids = [one.strip() for one in re.findall(r"<[^<>\s]+>", chain or "")]
    if not ids:
        return None
    artifact_id = (await db.execute(
        select(ReportPost.artifact_id).where(ReportPost.message_id.in_(ids))
        .order_by(ReportPost.id.desc()).limit(1))).scalar_one_or_none()
    if artifact_id is not None:
        return await _report(db, int(artifact_id))

    from ..models.artifact import ArtifactField, ArtifactValue
    artifact_id = (await db.execute(
        select(ArtifactValue.artifact_id)
        .join(ArtifactField, ArtifactField.id == ArtifactValue.field_id)
        .where(ArtifactField.key == "mail_ref", ArtifactValue.value_text.in_(ids))
        .limit(1))).scalar_one_or_none()
    return await _report(db, int(artifact_id)) if artifact_id is not None else None


async def _report(db: AsyncSession, artifact_id: int) -> Artifact | None:
    kind = await bugs_svc.ensure_type(db)
    artifact = await db.get(Artifact, artifact_id)
    return artifact if artifact is not None and artifact.type_id == kind.id else None


async def ours(db: AsyncSession, payload: dict) -> Artifact | None:
    """A mail that IS one of our answers, come back to us.

    It happens twice over: the copy in the Sent folder, and — when we answer somebody in the
    same mailbox — the delivered one. Both are pushed in by the watcher like any other mail,
    and without this they would travel on into the assistant as if a stranger had written
    them. They are already in the thread; there is nothing to do with them but recognise
    them.
    """
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        return None
    artifact_id = (await db.execute(
        select(ReportPost.artifact_id).where(ReportPost.message_id == message_id)
        .limit(1))).scalar_one_or_none()
    return await _report(db, int(artifact_id)) if artifact_id is not None else None


async def match(db: AsyncSession, payload: dict) -> tuple[Artifact, str] | None:
    """Which report this mail answers, and by which of the three ways it was recognised."""
    chain = " ".join(_header(payload, name) for name in ("In-Reply-To", "References"))
    found = await _by_reference(db, chain)
    if found is not None:
        return found, "reference"

    found = await _by_reference(db, " ".join(_addresses(payload)))
    if found is not None:
        return found, "address"

    found = await _by_known_id(db, chain)
    if found is not None:
        return found, "message-id"

    tag = SUBJECT_TAG.search(str(payload.get("subject") or ""))
    if tag:
        artifact = await _report(db, int(tag.group(1)))
        _, sender = _sender(payload)
        if artifact is not None and sender:
            known = (await bugs_svc.value_of(db, artifact.id, "reply_email")).lower()
            if known and known == sender:
                return artifact, "subject"
    return None


async def file_reply(db: AsyncSession, artifact: Artifact, payload: dict) -> ReportPost | None:
    """Write a mail into the thread of a report. None when it is already in there."""
    message_id = str(payload.get("message_id") or "").strip()
    body = body_of(payload)
    if await _already_in(db, artifact.id, message_id, body):
        return None

    name, address = _sender(payload)
    try:
        post = await bugs_svc.add_post(db, artifact, body, author_label=(name or address)[:200],
                                       via="mail", message_id=message_id)
    except IntegrityError:
        # Two deliveries of the same mail at the same moment: both looked, both saw nothing,
        # one wrote. The index decides, and the loser has nothing left to do.
        await db.rollback()
        return None
    # The reporting program shows the conversation to its own people, so it is told that
    # something was added — but under a name of its own. `answer` means "the team has
    # answered" over there and pushes a message to the reporter; for a sentence the reporter
    # wrote themselves that would be a notification about their own mail. A program that does
    # not know `post` ignores it, which is the right silence.
    await bugs_svc.notify_source(db, artifact, event="post", post=post)
    await _tell_the_house(db, artifact, post, address)
    log.info("report %s: reply by mail from %s", artifact.id, address)
    return post


async def _already_in(db: AsyncSession, artifact_id: int, message_id: str, body: str) -> bool:
    """Is this mail already an entry of this report?

    Delivered twice is a normal state of affairs with mail (a watcher that starts again, a
    server that repeats itself, an answer that lands in the inbox as well as in Sent); the
    `Message-ID` is what tells the second delivery from a second mail.

    Without one — a mail program that sets none — the text within a short window has to do.
    That is coarser: somebody who really writes the same sentence twice within a quarter of an
    hour loses the second. Weighed against a thread that shows everything twice, that is the
    better mistake, and it only ever happens to mail that arrives without an identity.
    """
    if message_id:
        twice = (await db.execute(select(ReportPost.id).where(
            ReportPost.artifact_id == artifact_id,
            ReportPost.message_id == message_id))).scalar_one_or_none()
        return twice is not None

    recently = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=15)
    same = (await db.execute(select(ReportPost.id).where(
        ReportPost.artifact_id == artifact_id, ReportPost.via == "mail",
        ReportPost.body == body.strip()[:bugs_svc.LIMITS["details"]],
        ReportPost.created_at >= recently))).scalar_one_or_none()
    return same is not None


async def _tell_the_house(db: AsyncSession, artifact: Artifact, post: ReportPost,
                          sender: str) -> None:
    """Die Glocke für den, der sich um diese Meldung kümmert.

    Ohne das wäre der Weg per Mail am Ende doch einer in eine Richtung: die Antwort landet im
    Verlauf, der Verlauf liegt in einer Liste, die niemand offen hat, und der Melder wartet.
    Wer gemeint ist, entscheidet `bugs.who_cares` — dieselbe Frage stellt sich bei einer
    Antwort aus dem Programm, und sie darf nicht zweimal verschieden beantwortet werden.
    """
    await ring(db, artifact, post, sender)


async def ring(db: AsyncSession, artifact: Artifact, post: ReportPost, sender: str) -> None:
    """Melden, dass in einer Meldung etwas gesagt wurde. Egal, durch welche Tür."""
    wer = await bugs_svc.who_cares(db, artifact)
    if not wer:
        return
    from ..models.notification import Notification
    db.add(Notification(
        user_id=wer, kind="report_reply",
        title=f"↩ BUG-{artifact.id}: {artifact.title}"[:500],
        body=f"{sender}\n{post.body[:500]}",
    ))
    await db.commit()


# ── A mail that answers nothing ──────────────────────────────────────────────

async def sources_by_address(db: AsyncSession) -> dict[str, BugSource]:
    """Our report addresses: which program answers under which address.

    Only addresses a project was deliberately given, and only projects that have a reporting
    program: the private mailbox of a person is watched by the same watcher, and a mail to it
    must never turn into a report — that would make a report out of every newsletter.

    Two programs of one project share the address. Which of them a mail is written down under
    is then decided by the order, and that is not an accident to fix: whoever wants to tell
    two ways in gives them two projects, which is exactly the line along which the reports are
    told apart everywhere else.
    """
    from ..models.project import Project
    rows = (await db.execute(
        select(BugSource, Project.reply_from)
        .join(Project, Project.id == BugSource.project_id)
        .where(BugSource.enabled.is_(True), Project.reply_from != "",
               Project.mail_account_id.is_not(None))
        .order_by(BugSource.id))).all()
    found: dict[str, BugSource] = {}
    for source, address in rows:
        found.setdefault(address.lower(), source)
    return found


def _is_machine_mail(payload: dict) -> bool:
    """Whether this is a bulk or an automatic mail.

    An out-of-office reply to our answer would otherwise become a report, whose answer would
    trigger the next one. The rule is the one the mail world agreed on for exactly this:
    whoever sets `Auto-Submitted` or `Precedence: bulk` is not asking a question.
    """
    if _header(payload, "Auto-Submitted").strip().lower() not in ("", "no"):
        return True
    if _header(payload, "Precedence").strip().lower() in ("bulk", "list", "junk"):
        return True
    return bool(_header(payload, "List-Id").strip())


async def new_from_mail(db: AsyncSession, payload: dict) -> Artifact | None:
    """Make a report out of a mail that came to a report address and answers nothing.

    This is the door for whoever has never seen the reporting program: they write a mail to
    the address that stands under it, and it lands in the same list as everything else,
    answerable the same way. The address is what limits this — see `sources_by_address`.
    """
    known = await sources_by_address(db)
    if not known:
        return None
    source = next((known[one] for one in _addresses(payload) if one in known), None)
    if source is None:
        return None
    if _is_machine_mail(payload):
        log.info("mail to %s ignored: bulk or automatic", source.key)
        return None
    name, address = _sender(payload)
    if not address or address in known:
        # Our own address as the sender is a loop, not a report.
        return None

    kind = await bugs_svc.ensure_type(db)
    if not await bugs_svc.within_limit(db, source, kind.id):
        log.warning("mail to %s ignored: the program is over its hourly limit", source.key)
        return None

    artifact = await bugs_svc.create_report(db, source, {
        "title": str(payload.get("subject") or "").strip() or f"Mail from {address}",
        # A mail is a question until somebody says otherwise: whoever writes about something
        # broken does not thereby say that it is.
        "kind": "question",
        "details": body_of(payload),
        "contact": (name and f"{name} <{address}>" or address),
        "reply_email": address,
    })
    # The text of the mail IS the report (field `details`), so it is not written a second
    # time as an entry. What is kept is its Message-ID — a further mail of the reporter
    # refers back to it, and without it that one would open a second report.
    message_id = str(payload.get("message_id") or "").strip()
    if message_id:
        await bugs_svc.set_field(db, artifact, "mail_ref", message_id[:400])
    log.info("report %s made out of a mail from %s to %s", artifact.id, address, source.key)
    return artifact
