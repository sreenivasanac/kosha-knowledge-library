"""Alembic environment: URL from Kosha settings, metadata from the models."""
from alembic import context
from sqlalchemy import create_engine

from kosha.config import get_settings
from kosha.models import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
