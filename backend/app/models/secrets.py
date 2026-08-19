import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ProviderToken(Base):
    """Named LLM token per provider and user. value_enc is Fernet encrypted.

    provider: claude_code (OAuth-Setup-Token) | codex (ChatGPT-JWT) | openai (sk-API-Key).
    Several per (user, provider) are possible; exactly one can be is_default.
    """
    __tablename__ = "provider_tokens"
    __table_args__ = (UniqueConstraint("user_id", "provider", "name", name="uq_provider_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    value_enc: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optionale eigene Base-URL (OpenAI-kompatibler Endpoint, z. B. lokales litellm).
    # Relevant only for the OpenAI family; empty or NULL means the provider default (api.openai.com).
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSecret(Base):
    """Secret vault: value_enc is Fernet encrypted. user_id NULL = a system secret.

    Referenceable as `secret:<name>` in token fields.
    """
    __tablename__ = "user_secrets"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_secret"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    value_enc: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
