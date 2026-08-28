"""Programs that are allowed to report a bug from outside.

The report itself is an artifact of the type `bug` and needs no table of its own: title,
state and all the fields hang off the register like with every other artifact. What does
need one is the sender. A program that runs on the machine of a stranger (the web interface
of a device programmer, for example) carries no user session, so it identifies itself with
a token of its own, and that token has to be revocable without touching an account.

The token is stored as a hash for the same reason a password is: whoever gets to read the
database still cannot report in the name of the program.
"""
import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class BugSource(TimestampMixin, Base):
    __tablename__ = "bug_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Short handle the program sends along, and what the list groups by.
    key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), default="")
    # Last characters of the token in plain text. The token itself is shown exactly once, and
    # afterwards this is the only way to tell two of them apart in the interface.
    token_hint: Mapped[str] = mapped_column(String(12), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Which project takes care of these reports. Required, and not out of tidiness: the
    # project carries the address the reports are answered from and the tickets that grow out
    # of them. A program without a project reports into nothing.
    #
    # `RESTRICT`: a project that still carries a reporting program cannot be deleted by the
    # way. Whoever wants to be rid of it moves the program first — the alternative would be
    # a token in a stranger's device that reports into a hole.
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    # How many reports this program may send per hour. A form open to everybody needs a
    # ceiling, and one per program keeps a chatty app from silencing the others.
    hourly_limit: Mapped[int] = mapped_column(Integer, default=20)
    description: Mapped[str] = mapped_column(Text, default="")
    # Where the program wants to be told that something happened to one of its reports.
    # Without it the reporter would have to keep looking by themselves whether an answer has
    # arrived, and nobody does that.
    callback_url: Mapped[str] = mapped_column(String(500), default="")


class ReportPost(TimestampMixin, Base):
    """One entry in the conversation about a report.

    The report itself is not a post: its text stands in the field `details`, the way the
    reporting program sent it. Here hangs what came afterwards, from both sides, in the order
    it was written.

    `internal` is the reason this table has to know both sides apart. A note among ourselves
    ("the log shows he never had the profile") must never travel to the reporter, and one
    forgotten check would send it. So it is not a matter of who asks: the app endpoints
    filter it out, always, and there is no parameter to switch that off.
    """
    __tablename__ = "report_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    # Written here, in Traccoon.
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Written over there, in the reporting program. The label is what the reader sees, the
    # reference is how the program recognises its own user again.
    author_label: Mapped[str] = mapped_column(String(200), default="")
    external_ref: Mapped[str] = mapped_column(String(120), default="", index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    # Which way this entry took: `web` (written here), `app` (the reporting program),
    # `mail` (an answer that came in by mail). The reader has to be able to tell them apart —
    # answering a mail reply in the app only would be a conversation that stops without
    # anybody noticing.
    via: Mapped[str] = mapped_column(String(10), default="web")
    # The `Message-ID` of the mail this entry went out as, or came in as. It is what the
    # answer of the reporter carries back in `In-Reply-To`, and therefore the thread of the
    # conversation: without it every reply would be a mail without a home.
    message_id: Mapped[str] = mapped_column(String(400), default="", index=True)


# One mail, one entry: the same Message-ID must not land in the same report twice, and a
# check in the code loses the race when two deliveries arrive in the same moment. Entries
# without a Message-ID (everything written here in the house) are outside the rule.
Index("uq_report_post_message", ReportPost.artifact_id, ReportPost.message_id, unique=True,
      postgresql_where=text("message_id <> ''"), sqlite_where=text("message_id <> ''"))


class ReportRead(Base):
    """Bis wohin eine Person die Unterhaltung einer Meldung gelesen hat.

    Gelesen ist etwas Persönliches: dass der eine die Antwort des Melders gesehen hat, sagt
    nichts darüber, ob der andere sie kennt. Deshalb eine Zeile je Person und Meldung und
    nicht ein Häkchen an der Meldung selbst.

    Gespeichert wird die Nummer des zuletzt gesehenen Eintrags und nicht ein Zeitpunkt: die
    Nummern laufen hoch, Zeitstempel können bei zwei Zustellungen in derselben Sekunde
    gleich sein — und dann wäre eine Antwort für immer ungelesen oder für immer gelesen.

    Nur eine Zeile, wenn jemand hingesehen hat. Wer eine Meldung noch nie geöffnet hat, hat
    keine, und dann ist alles vom Melder neu.
    """
    __tablename__ = "report_reads"
    __table_args__ = (UniqueConstraint("user_id", "artifact_id", name="uq_report_read"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    last_post_id: Mapped[int] = mapped_column(Integer, default=0)
    seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class ReportImage(Base):
    """A picture belonging to a post.

    In the database and not on a disk, like the ticket attachments (`attachments.data`): the
    pictures of a report are few and small, and a file store is a second thing to back up,
    to migrate and to lose.
    """
    __tablename__ = "report_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # A picture hangs off an answer - or off the report itself, because the report is not an
    # answer: its text stands in the field `details`, and the screenshot that came with it
    # would have nowhere to go. Exactly one of the two is set.
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("report_posts.id", ondelete="CASCADE"), nullable=True, index=True)
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(300), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="image/png")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
