from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEDGERLINE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ledgerline:ledgerline@localhost:5432/ledgerline"
    secret_key: str
    session_max_age_seconds: int = 8 * 60 * 60
    cookie_secure: bool = True
    max_upload_bytes: int = 10 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
