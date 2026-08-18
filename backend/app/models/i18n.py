"""Translation overrides kept in the database.

The base catalogs live in the repository (`frontend/src/i18n/*.json`), because a text is
part of the source: it changes with the code it describes, and it belongs in the same
review. What sits here are the deviations from those catalogs plus whole languages nobody
shipped: an admin translates in the browser and does not need a release for it.

Precedence at runtime: override from here, then the shipped catalog of the language, then
the German source, then the key itself. Nothing ever renders empty.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class UiLocale(TimestampMixin, Base):
    """A language that exists in this installation.

    The shipped ones (German, English) exist without a row here. A row appears when an admin
    creates a language of their own, renames one or switches it off. Without this table a
    language would only exist through its texts, and an empty new language would vanish
    between two saves, which is exactly when somebody starts translating.
    """
    __tablename__ = "ui_locales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    locale: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class UiTranslation(TimestampMixin, Base):
    __tablename__ = "ui_translations"
    __table_args__ = (UniqueConstraint("locale", "key", name="uq_ui_translation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
