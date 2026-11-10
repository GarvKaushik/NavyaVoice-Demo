"""Alembic environment: runs migrations on the async engine from settings.DATABASE_URL."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from config.settings import get_settings
from database import models  # noqa: F401  (register models)
from database.session import Base

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(get_settings().DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


asyncio.run(_run_online())
