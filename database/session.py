"""Database engine setup, session management, and lifecycle hooks.

Engines are created lazily so tests and tools can point the app at another
database (e.g. SQLite) with `configure_database(url)` before first use.
"""

import os
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from config.settings import get_settings

Base = declarative_base()

_engine: Optional[AsyncEngine] = None
_read_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker] = None
_read_sessionmaker: Optional[async_sessionmaker] = None


def _make_engine(url: str) -> AsyncEngine:
    settings = get_settings()
    if url.startswith("sqlite") or os.environ.get("TEST_DATABASE_URL"):
        from sqlalchemy.pool import NullPool

        return create_async_engine(url, echo=False, poolclass=NullPool)  # safe across event loops (tests)
    return create_async_engine(
        url,
        echo=False,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args={"timeout": 3},  # a stopped server must not hold up a call
    )


def configure_database(url: Optional[str] = None, read_url: Optional[str] = None) -> None:
    """(Re)creates engines. Called implicitly on first use with settings values."""
    global _engine, _read_engine, _sessionmaker, _read_sessionmaker
    settings = get_settings()
    _engine = _make_engine(url or settings.DATABASE_URL)
    _sessionmaker = async_sessionmaker(bind=_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    replica = read_url if read_url is not None else settings.DATABASE_READ_URL
    if replica and not (url or "").startswith("sqlite"):
        _read_engine = _make_engine(replica)
        _read_sessionmaker = async_sessionmaker(bind=_read_engine, class_=AsyncSession, expire_on_commit=False)
    else:
        _read_engine, _read_sessionmaker = None, None


def get_engine() -> AsyncEngine:
    if _engine is None:
        configure_database()
    return _engine


def get_sessionmaker() -> async_sessionmaker:
    if _sessionmaker is None:
        configure_database()
    return _sessionmaker


def get_read_sessionmaker() -> async_sessionmaker:
    """Read replica when configured, otherwise the primary."""
    if _sessionmaker is None:
        configure_database()
    return _read_sessionmaker or _sessionmaker


def AsyncSessionLocal() -> AsyncSession:
    """Opens a session on the primary (kept for existing call sites)."""
    return get_sessionmaker()()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency provider for FastAPI route handlers."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Creates tables directly (tests / local only). Production uses Alembic migrations."""
    from . import models  # noqa: F401  (register models)

    engine = get_engine()
    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text

            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engines() -> None:
    for engine in (_engine, _read_engine):
        if engine is not None:
            await engine.dispose()
