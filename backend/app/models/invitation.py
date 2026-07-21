import datetime as dt

from sqlalchemy import (
    DateTime, Enum as SAEnum, ForeignKey, Integer, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .enums import ProjectRole, pg_enum_values


class InvitationStatus:
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"


class ProjectInvitation(Base):
    """Einladung eines Users per E-Mail in ein Projekt (Token-Link)."""
    __tablename__ = "project_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[ProjectRole] = mapped_column(
        SAEnum(ProjectRole, name="projectrole", values_callable=pg_enum_values),
        default=ProjectRole.member, nullable=False,
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    invited_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default=InvitationStatus.pending, nullable=False)
    accepted_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
