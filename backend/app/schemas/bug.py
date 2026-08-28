"""Shapes of the bug endpoints.

The intake shape (`BugReportIn`) is the one thing here that strangers program against: it
lands in the web interface of a device programmer and in whatever reports next. Fields may
be added to it, never renamed, or a program somebody installed months ago starts failing at
a wording change.
"""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class BugReportIn(BaseModel):
    """What a reporting program sends."""
    title: str = Field(min_length=1)
    # bug | feature | question. Unknown values become "bug": a report that arrives with a
    # kind nobody knows is still a report.
    kind: str = "bug"
    details: str = ""
    # Who ran into it. Required by the form, because with these reports the question back is
    # half the work: a callsign, a name or a mail address, whatever the reporter wants to give.
    contact: str = Field(min_length=1)
    version: str = ""
    environment: str = ""
    # Whatever the program can add by itself and a person could not type: a CAT log, a stack
    # trace, the answer of a device.
    technical: str = ""
    # Who reported it over there. The reference is how the program finds its own user again
    # (a game, say: the player id), the contact is what a human reads.
    external_ref: str = ""
    # Where the reporter reads an answer. A program that knows the mail address of its user
    # sends it along; without it the answer only exists in the program (and in Traccoon),
    # and whoever does not come back never learns it.
    reply_email: str = ""


class BugReportAck(BaseModel):
    """What the reporting program gets back. Deliberately thin: the reporter is a stranger,
    the number is only there so they can name it in a mail."""
    id: int
    number: str


class BugOut(BaseModel):
    id: int
    title: str
    images: list[dict] = []
    status: str
    kind: str
    app: str
    version: str
    contact: str
    environment: str
    details: str
    technical: str
    ticket: str
    project_id: int | None
    created_at: dt.datetime | None
    # Where an answer would go by mail — and whether it would go at all. `mail_ready` is
    # false when the address is there but no mailbox answers for this report; the interface
    # has to say that BEFORE somebody writes, not afterwards.
    reply_email: str = ""
    mail_ready: bool = False
    # Wie viele Einträge der Gegenseite der Leser noch nicht gesehen hat. Was wir selbst
    # geschrieben haben, zählt nie: wir waren dabei.
    unread: int = 0


class BugStatusIn(BaseModel):
    status: str


class BugIn(BaseModel):
    """A report opened here instead of arriving from somewhere.

    Everything about it is optional except the heading and, in practice, a way to reach the
    other side: a conversation with nobody to talk to is a note.
    """
    title: str = Field(min_length=1)
    kind: str = "question"
    details: str = ""
    contact: str = ""
    reply_email: str = ""
    # Out of which of one's own mailboxes, and under which address. Without both the report
    # stays inside the house and is answered where it can be read. With a project that has a
    # mailbox of its own, both may stay empty — then that one applies.
    account_id: int | None = None
    mail_from: str = ""
    project_id: int | None = None


class BugReporterIn(BaseModel):
    """Correcting who the reporter is and how they are reached.

    Needed because most reports arrive without an address: a callsign in `contact`, and the
    mail address only in the third sentence of the text.
    """
    contact: str | None = None
    reply_email: str | None = None


class BugToTicketIn(BaseModel):
    project_id: int
    summary: str = ""
    description: str = ""


class DraftIn(BaseModel):
    """Woran der Entwurf sich halten soll, über die Unterhaltung hinaus.

    Zwei Dinge, und sie sind nicht dasselbe: `draft` ist der Text, wie er gerade im Feld
    steht, `comments` sind die Anmerkungen dazu ("kürzer", "frag nach der Version", "nicht
    so förmlich"). Mit Entwurf ist es eine Überarbeitung, ohne eine erste Fassung.

    Die Anmerkungen kommen als Liste und nicht als ein Text: sie sind über mehrere Runden
    entstanden, und ihre Reihenfolge ist die Reihenfolge, in der jemand sie gesagt hat. Die
    letzte ist die frischeste — sie sticht eine frühere, wenn beide sich widersprechen.
    """
    draft: str = ""
    comments: list[str] = []


class DraftOut(BaseModel):
    """A proposal, nothing more.

    There is deliberately no endpoint that writes this into the thread or sends it: a draft
    becomes an answer by a person reading it, changing it and pressing the button — the same
    button as for one they typed themselves.
    """
    text: str
    # Which agent wrote it. Stands here so that a bad draft can be traced to a model instead
    # of to the feature.
    agent: str = ""


class BugSourceIn(BaseModel):
    key: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1)
    callback_url: str = ""
    # Required: a program reports INTO a project. The project carries the address its reports
    # are answered from and the board the tickets grow on — without one a report lands
    # nowhere and can be answered by nobody.
    project_id: int
    hourly_limit: int = 20
    description: str = ""
    enabled: bool = True


class BugSourceOut(BaseModel):
    id: int
    key: str
    name: str
    callback_url: str = ""
    # The address this program answers from — it belongs to the project and is only shown
    # here, where the question "how is this program reachable" is asked.
    reply_email: str = ""
    project_id: int | None
    hourly_limit: int
    description: str
    enabled: bool
    token_hint: str
    # Only ever filled right after creating or renewing: afterwards nothing but the hash is
    # left, and that on purpose.
    token: str | None = None


class PostOut(BaseModel):
    id: int
    body: str
    author: str
    internal: bool
    # Which way this entry took: `web`, `app` or `mail`. The reporting program does not care,
    # the list here does: whoever answers wants to see that the last sentence came in by
    # mail and will be read there.
    via: str = "web"
    # Written here, in Traccoon. The reporting program shows its own people who is talking to
    # them: the team, or somebody like themselves.
    team: bool = False
    mine: bool = False
    images: list[dict] = []
    created_at: dt.datetime | None


class PostIn(BaseModel):
    body: str = Field(min_length=1)
    internal: bool = False


class AppPostIn(BaseModel):
    """A reply written in the reporting program, on behalf of one of its users."""
    body: str = Field(min_length=1)
    external_ref: str = Field(min_length=1)
    author: str = ""


class ThreadOut(BaseModel):
    """What the reporting program shows its user: the report and what was said about it."""
    id: int
    title: str
    kind: str
    status: str
    details: str
    # Who reported it, as a human reads it. The program shows this to whoever looks after
    # its reports; for the reporter themselves it is their own name.
    contact: str = ""
    images: list[dict] = []
    created_at: dt.datetime | None
    # When something last happened. The reporting program sorts its list by it, so a report
    # that just got an answer must move up.
    updated_at: dt.datetime | None
    posts: list[PostOut]


class BugAppCount(BaseModel):
    """How much of one kind came out of one reporting program. Empty program = unnamed."""
    app: str = ""
    count: int


class BugKindCount(BaseModel):
    """How many open reports of one kind wait, and where they came from.

    The programs travel with the figure because the number alone says only that something
    is waiting: three broken things out of one program are one fault, three out of three
    programs are three.
    """
    kind: str = ""
    count: int
    apps: list[BugAppCount] = []
