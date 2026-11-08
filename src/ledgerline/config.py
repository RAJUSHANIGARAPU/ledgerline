from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEDGERLINE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ledgerline:ledgerline@localhost:5432/ledgerline"
    secret_key: str
    session_max_age_seconds: int = 8 * 60 * 60
    cookie_secure: bool = True
    max_upload_bytes: int = 10 * 1024 * 1024
    # Public HTTPS address, used in the phone shortcut instructions.
    public_base_url: str = "http://127.0.0.1:8000"

    # Mailbox polled for incoming invoices (empty host = email intake off).
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    imap_processed_folder: str = "Ledgerline/Processed"
    imap_poll_seconds: int = 300
    # Mail to user+<administration id>@domain goes to that administration; otherwise this one.
    imap_default_administration_id: int = 1
    # Only accept mail from these senders (comma-separated addresses or @domains); empty = any.
    imap_allowed_senders: str = ""

    # Document extraction from photos and scans: "none" or "anthropic" (opt-in, sends documents
    # to the API provider). The API key is read from ANTHROPIC_API_KEY by the SDK.
    extraction_provider: str = "none"
    extraction_model: str = "claude-opus-5-5"
    extraction_monthly_limit: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
