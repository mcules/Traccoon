"""Shapes of the bug endpoints.

The intake shape (`BugReportIn`) is the one thing here that strangers program against: it
lands in the web interface of the Devprog programmer and in whatever reports next. Fields may
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
    # (gameproj: the player id), the contact is what a human reads.
    external_ref: str = ""


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


class BugStatusIn(BaseModel):
    status: str


class BugToTicketIn(BaseModel):
    project_id: int
    summary: str = ""
    description: str = ""


class BugSourceIn(BaseModel):
    key: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1)
    callback_url: str = ""
    project_id: int | None = None
    hourly_limit: int = 20
    description: str = ""
    enabled: bool = True


class BugSourceOut(BaseModel):
    id: int
    key: str
    name: str
    callback_url: str = ""
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
