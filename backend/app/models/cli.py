"""Claude CLI sessions of projects in CLI mode (`projects.cli_mode`).

One session per (project, person): a container with an interactive Claude Code in tmux,
started by the deployer. Released tickets are delivered into the session of the person who
released them, instead of going to the agents of the worker.
"""
import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class CliSession(TimestampMixin, Base):
    __tablename__ = "cli_sessions"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_cli_session_project_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    container: Mapped[str] = mapped_column(String(120), default="")
    # What Traccoon last saw: stopped | starting | running | failed. The truth is the
    # container itself; this is only what the list shows without asking Docker every time.
    status: Mapped[str] = mapped_column(String(20), default="stopped")
    error: Mapped[str] = mapped_column(Text, default="")
    # The token the session's ticket tools log in with (/api/mcp/project). Renewed on every
    # start, so a stopped session's token is worthless. Stored encrypted plus a hash to find it.
    mcp_token_enc: Mapped[str] = mapped_column(Text, default="")
    mcp_token_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    last_attach_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CliDelivery(Base):
    """One ticket on its way into a session: waiting, delivered, reported.

    The workflow token of the ticket waits on the agent node meanwhile, exactly as it waits
    for a worker run; `task_id` is the key the engine waits on, and `ticket_report` answers
    under it (see services/cli_sessions.report).
    """
    __tablename__ = "cli_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("cli_sessions.id", ondelete="CASCADE"), index=True)
    # A ticket, or (issue_id NULL) the deploy job of a release.
    issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), nullable=True, index=True)
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("releases.id", ondelete="CASCADE"), nullable=True, index=True)
    task_id: Mapped[str] = mapped_column(String(120), unique=True)
    # now | queue, keep | clear: copied from the ticket at the moment of release, so changing
    # the ticket afterwards does not reorder what is already waiting.
    delivery: Mapped[str] = mapped_column(String(10), default="queue")
    context: Mapped[str] = mapped_column(String(10), default="keep")
    position: Mapped[int] = mapped_column(Integer, default=0)
    # waiting | delivered | reported | cancelled
    state: Mapped[str] = mapped_column(String(20), default="waiting", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Release(Base):
    """A set of tickets deployed together (projects in CLI mode).

    Released tickets collect in the project's open release. Deploying it hands one deploy
    job to a session, behind whatever that session is still working on; the session answers
    with `release_report`. After that a fresh open release is started, unless the project
    says otherwise (`projects.release_auto_new`).
    """
    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_release_project_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(120), default="")
    # open | deploying | deployed | failed
    state: Mapped[str] = mapped_column(String(20), default="open", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    deployed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deploy_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
