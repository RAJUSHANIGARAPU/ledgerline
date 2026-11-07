from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from ledgerline import models  # noqa: F401  (registers tables)
from ledgerline.db import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from ledgerline.config import get_settings

    return get_settings().database_url


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
