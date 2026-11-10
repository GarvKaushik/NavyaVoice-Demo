"""Test isolation: temporary SQLite database and in-memory Redis (never the dev services).
Set TEST_DATABASE_URL to run the suite on a throwaway Postgres database instead."""

import asyncio
import os
import tempfile

import pytest

import services.redis_service as redis_service
from database.session import configure_database


@pytest.fixture(scope="session", autouse=True)
def isolated_backends():
    tmp = tempfile.mkdtemp()
    configure_database(os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{os.path.join(tmp, 'test.db')}",
                       read_url="")
    service = redis_service.RedisService("redis://127.0.0.1:1/0")
    service._retry_at = float("inf")  # never connect; in-memory store only
    redis_service._instance = service

    from database.seed_data import seed_database

    asyncio.run(seed_database(reset=False))
    yield
