import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ProviderToken(Base):
    """Benannter LLM-Token je Provider und User. value_enc Fernet-verschlüsselt.

    provider: claude_code (OAuth-Setup-Token) | codex (ChatGPT-JWT) | openai (sk-API-Key).
    Mehrere je (user, provider) möglich; genau einer kann is_default sein.
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
    # Nur für die OpenAI-Familie relevant; leer/NULL → Provider-Default (api.openai.com).
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSecret(Base):
    """Secret-Tresor: value_enc ist Fernet-verschlüsselt. user_id NULL = System-Secret.

    Referenzierbar als `secret:<name>` in Token-Feldern.
    """
    __tablename__ = "user_secrets"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_secret"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    value_enc: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
