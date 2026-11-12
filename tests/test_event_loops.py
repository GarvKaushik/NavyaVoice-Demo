"""Each call in the worker runs on its own event loop (a thread on Windows); shared clients must not cross loops."""

import asyncio
import threading

from sqlalchemy import text

from database.session import get_read_sessionmaker, get_sessionmaker


def _in_new_loop(coro_fn):
    """Runs a coroutine on a fresh event loop in another thread, like a worker job."""
    result = {}

    def run():
        result["value"] = asyncio.run(coro_fn())

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    return result["value"]


async def _query():
    async with get_read_sessionmaker()() as db:
        value = (await db.execute(text("SELECT 1"))).scalar()
    return id(get_sessionmaker()), value


def test_each_event_loop_gets_its_own_database_engine():
    first_maker, first = _in_new_loop(_query)
    second_maker, second = _in_new_loop(_query)
    assert first == second == 1  # the second loop's query works
    assert first_maker != second_maker


async def test_the_same_loop_reuses_its_engine():
    assert get_sessionmaker() is get_sessionmaker()
