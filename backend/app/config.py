from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure: credentials exclusively from the environment (.env), no defaults in the code.
    database_url: str = ""   # DATABASE_URL (from .env or compose), for instance postgresql+asyncpg://<user>:<pw>@db:5432/<db>
    redis_url: str = ""      # REDIS_URL (from .env or compose)

    # Auth and crypto: have to come from the environment; empty = clearly unconfigured.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    secret_encryption_key: str = ""  # Fernet; empty = plain text (dev only)

    allowed_origins: str = "*"

    # Bootstrap admin (only when 0 users exist)
    bootstrap_admin_email: str = ""
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""

    # Dev: tables over create_all instead of Alembic
    dev_create_all: bool = True

    # Public base URL (for invitation links and the like), for instance https://traccoon.example.com
    app_base_url: str = ""

    # The mail webhook and the assistant are configured completely over the web UI and the
    # database (WebhookSub plus classifying and handler agent): deliberately NO MAIL_* env or file config any more.

    # MCPJungle: self-service provisioning of the user MCP group (backend on the mcp-backends network).
    mcpjungle_base: str = "http://mcpjungle:8080"   # MCPJUNGLE_BASE
    mcpjungle_admin_token: str = ""                 # MCPJUNGLE_ADMIN_TOKEN

    # --- SMTP (E-Mail-Versand, z. B. Projekt-Einladungen) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""       # Absenderadresse, Default = smtp_user
    smtp_use_tls: bool = True

    @property
    def cors_origins(self) -> list[str]:
        raw = self.allowed_origins.strip()
        if raw in ("", "*"):
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
