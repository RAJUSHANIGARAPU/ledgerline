from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class OperatorIdentity:
    legal_name: str
    kvk_number: str
    address: str
    contact_email: str

    def missing(self) -> list[str]:
        """Names of the fields that are empty or still a bracketed placeholder."""
        return [name for name, value in vars(self).items()
                if not value or value.startswith("[")]


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

    # Document extraction from photos and scans: "none", "anthropic" or "openrouter" (opt-in,
    # sends documents to the API provider). Keys: ANTHROPIC_API_KEY or OPENROUTER_API_KEY.
    extraction_provider: str = "none"
    extraction_model: str = "claude-haiku-5-5"
    extraction_monthly_limit: int = 200

    # Operations: files shared between app, worker and backup job (a compose volume).
    ops_dir: str = "/var/lib/ledgerline/ops"
    # Dead-man's-switch URL the worker pings every healthcheck_ping_seconds (empty = off).
    healthcheck_ping_url: str = ""
    healthcheck_ping_seconds: int = 300
    # /healthz/deep thresholds.
    health_worker_max_age_seconds: int = 120
    health_backup_max_age_hours: int = 26
    health_min_free_disk_percent: int = 10
    # Fail the deep check when the last backup did not reach the off-site repository.
    health_require_offsite_backup: bool = False
    health_rate_limit_per_minute: int = 30

    # Who runs this installation: shown on the privacy notice and the terms. Production health
    # fails until all four are set (they are not guessed or defaulted).
    operator_legal_name: str = ""
    operator_kvk_number: str = ""
    operator_address: str = ""
    operator_contact_email: str = ""

    # Outbound email (invitations, password reset). Empty host = email off.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # "starttls" (port 587), "ssl" (port 465) or "none" (trusted local relay only).
    smtp_tls: str = "starttls"
    # "development" prints links to the server log when SMTP is not configured.
    environment: str = "production"

    @property
    def operator_identity(self) -> "OperatorIdentity":
        return OperatorIdentity(
            legal_name=self.operator_legal_name.strip(),
            kvk_number=self.operator_kvk_number.strip(),
            address=self.operator_address.strip(),
            contact_email=self.operator_contact_email.strip(),
        )

    @property
    def owner_database_url(self) -> str:
        return self.migration_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
