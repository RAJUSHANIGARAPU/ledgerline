from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEDGERLINE_", env_file=".env", extra="ignore")

    # Application and worker connection: the restricted `ledgerline_app` role.
    database_url: str = "postgresql+psycopg://ledgerline:ledgerline@localhost:5432/ledgerline"
    # Migrations and `ensure-app-role` connect as the database owner. Unset = use database_url,
    # which only works for local development where one superuser role does everything.
    migration_database_url: str | None = None
    # Password given to the `ledgerline_app` role by `ledgerline ensure-app-role`.
    app_db_password: str = ""
    secret_key: str
    session_max_age_seconds: int = 8 * 60 * 60
    cookie_secure: bool = True
    max_upload_bytes: int = 10 * 1024 * 1024
    # Public HTTPS address, used in the phone shortcut instructions.
    public_base_url: str = "http://127.0.0.1:8000"
    # Source link shown to users (AGPL-3.0 section 13); point it at your fork if you modify it.
    source_url: str = "https://github.com/RAJUSHANIGARAPU/ledgerline"

    # Mailbox polled for incoming invoices (empty host = email intake off).
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    imap_processed_folder: str = "Ledgerline/Processed"
    imap_poll_seconds: int = 300
    # Mail to user+<inbox token>@domain goes to that administration. Mail without a valid token
    # goes to this one on single-tenant installs and is rejected when several tenants exist.
    imap_default_administration_id: int = 1
    # Only accept mail from these senders (comma-separated addresses or @domains); empty = any.
    imap_allowed_senders: str = ""

    # Document extraction from photos and scans: "none" or "anthropic" (opt-in, sends documents
    # to the API provider). The API key is read from ANTHROPIC_API_KEY by the SDK.
    extraction_provider: str = "none"
    extraction_model: str = "claude-opus-5-5"
    extraction_monthly_limit: int = 200


    @property
    def owner_database_url(self) -> str:
        return self.migration_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
