from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastruktur — Zugangsdaten ausschließlich aus der Umgebung (.env), keine Defaults im Code.
    database_url: str = ""   # DATABASE_URL (aus .env/compose), z. B. postgresql+asyncpg://<user>:<pw>@db:5432/<db>
    redis_url: str = ""      # REDIS_URL (aus .env/compose)

    # Auth / Krypto — müssen aus der Umgebung kommen; leer = klar erkennbar unkonfiguriert.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    secret_encryption_key: str = ""  # Fernet; leer = Klartext (nur Dev)

    allowed_origins: str = "*"

    # Bootstrap-Admin (nur wenn 0 User existieren)
    bootstrap_admin_email: str = ""
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""

    # Dev: Tabellen per create_all statt Alembic
    dev_create_all: bool = True

    # Öffentliche Basis-URL (für Einladungslinks etc.), z. B. https://traccoon.example.com
    app_base_url: str = ""

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
