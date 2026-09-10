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

    # Android apps that log in with this house's passkeys, `<package>:<SHA-256 of the signing
    # certificate>` (the colon form `keytool -list -v` prints), several separated by commas.
    # One line, two uses: the app's WebAuthn origin (`android:apk-key-hash:...`) and the
    # `/.well-known/assetlinks.json` Android reads before it lets the app use the site's keys.
    android_apps: str = ""                          # ANDROID_APPS

    # The mail webhook and the assistant are configured completely over the web UI and the
    # database (WebhookSub plus classifying and handler agent): deliberately NO MAIL_* env or file config any more.

    # MCPJungle: self-service provisioning of the user MCP group (backend on the mcp-backends network).
    # What the vault is called in the tree. Without it the folder name shows,
    # and a folder is named after where it lies rather than what it is.
    notes_vault_name: str = ""                      # NOTES_VAULT_NAME
    # Where the settings of the two note languages sit inside a vault: which
    # checkbox characters exist and what each means, how a missing value is
    # written. Both are paths inside the vault and both are empty by default,
    # which is the state a new vault is in — it has no such folder at all. The
    # folder belongs to a program that is being switched off, so its name is
    # configuration and not something this repository carries.
    notes_query_settings_dir: str = ""              # NOTES_QUERY_SETTINGS_DIR
    notes_task_settings_dir: str = ""               # NOTES_TASK_SETTINGS_DIR
    # Where the folder-to-template mapping sits: which form a new note gets from
    # the folder it is created in. Same shape as the two above, and empty for
    # the same reason — a vault that has no such folder simply has no mapping.
    notes_template_settings_dir: str = ""           # NOTES_TEMPLATE_SETTINGS_DIR
    # Where the theme's own switches are kept — what decides whether the folder
    # tree is coloured, and how. Read only, and only for the look.
    notes_style_settings_dir: str = ""              # NOTES_STYLE_SETTINGS_DIR
    # The vault's own configuration folder. Two settings are read from it and
    # both are about writing: where an attachment belongs, and whether links
    # follow a note that is renamed. Empty is a valid state and means the
    # defaults, which is what a vault that configures nothing should behave like.
    notes_config_dir: str = ""                      # NOTES_CONFIG_DIR
    # Where the text a save replaces is kept, outside the vault. Empty means
    # nothing is kept: writing somebody's notes to a second place is not a thing
    # to start doing without being asked.
    notes_recovery_dir: str = ""                    # NOTES_RECOVERY_DIR
    # The repository the older versions of a note are read out of. It sits
    # beside the vault, not inside it, and is mounted read only: a repository
    # inside a vault is carried to every device by the synchronisation. Empty
    # means a vault without a history, which is what a new one has.
    notes_history_dir: str = ""                     # NOTES_HISTORY_DIR

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
