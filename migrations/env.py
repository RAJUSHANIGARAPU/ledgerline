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

    return get_settings().owner_database_url


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    # Foreign keys between tenant tables are composite (tenant_id, id) in the database (0005) but
    # single-column in the models, so the ORM keeps simple relationships. Migrations write them by
    # hand; tests/tenancy guards that none is single-column.
    if type_ == "foreign_key_constraint":
        return False
    return not (type_ == "unique_constraint" and name and name.endswith("_tenant_id_id"))


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
