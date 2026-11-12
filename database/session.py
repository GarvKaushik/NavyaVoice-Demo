"""Database engine setup, session management, and lifecycle hooks.

Engines are created lazily so tests and tools can point the app at another
database (e.g. SQLite) with `configure_database(url)` before first use.

Each event loop gets its own engines. Pooled asyncpg connections belong to the loop that opened them, and the
call worker runs every call on a loop of its own (in a thread on Windows), so one shared pool fails from the
second call on.
"""

import asyncio
import os
import weakref
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

_urls: Optional[tuple] = None  # (url, read_url) given to configure_database
_by_loop: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()  # event loop -> its engines
_no_loop: Optional[dict] = None  # engines used outside any event loop


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
    """Points the app at a database. Engines are created per event loop on first use."""
    global _urls, _no_loop
    _urls = (url, read_url)
    _by_loop.clear()
    _no_loop = None


def _build(url: Optional[str], read_url: Optional[str]) -> dict:
    settings = get_settings()
    engine = _make_engine(url or settings.DATABASE_URL)
    bundle = {"engine": engine, "read_engine": None, "read_sessionmaker": None,
              "sessionmaker": async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False,
                                                 autoflush=False)}
    replica = read_url if read_url is not None else settings.DATABASE_READ_URL
    if replica and not (url or "").startswith("sqlite"):
        bundle["read_engine"] = _make_engine(replica)
        bundle["read_sessionmaker"] = async_sessionmaker(bind=bundle["read_engine"], class_=AsyncSession,
                                                         expire_on_commit=False)
    return bundle


def _bundle() -> dict:
    """The engines that belong to the running event loop."""
    global _no_loop
    if _urls is None:
        configure_database()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        if _no_loop is None:
            _no_loop = _build(*_urls)
        return _no_loop
    bundle = _by_loop.get(loop)
    if bundle is None:
        bundle = _by_loop[loop] = _build(*_urls)
    return bundle


def get_engine() -> AsyncEngine:
    return _bundle()["engine"]


def get_sessionmaker() -> async_sessionmaker:
    return _bundle()["sessionmaker"]


def get_read_sessionmaker() -> async_sessionmaker:
    """Read replica when configured, otherwise the primary."""
    bundle = _bundle()
    return bundle["read_sessionmaker"] or bundle["sessionmaker"]


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
    """Closes the running loop's engines (another loop's connections can only be closed on that loop)."""
    bundle = _bundle()
    for engine in (bundle["engine"], bundle["read_engine"]):
        if engine is not None:
            await engine.dispose()
