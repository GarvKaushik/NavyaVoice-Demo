"""Background service: dialing, CRM webhooks, post-call summaries and recording retention.

    python -m campaign.background
"""

import asyncio
import logging
from typing import Awaitable, Callable

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from campaign.dial_service import DialService
from config.settings import get_settings
from database.models import CallRecord, utcnow
from database.session import AsyncSessionLocal
from integrations.crm_webhook import deliver_pending
from observability.logging_setup import setup_logging
from observability.recording import delete_recording, retention_cutoff
from offline.post_call_analyzer import PostCallAnalyzer

logger = logging.getLogger(__name__)


async def delete_expired_recordings(limit: int = 100) -> int:
    deleted = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(CallRecord).where(
            CallRecord.recording_url.is_not(None), CallRecord.recording_deleted_at.is_(None),
            CallRecord.start_time < retention_cutoff(utcnow())).limit(limit))).scalars().all()
        for rec in rows:
            if delete_recording(rec.recording_url):
                rec.recording_deleted_at = utcnow()
                deleted += 1
        await db.commit()
    return deleted


async def _every(seconds: float, name: str, fn: Callable[[], Awaitable[object]]) -> None:
    while True:
        try:
            await fn()
        except Exception:
            logger.exception(f"{name} failed; retrying")
        await asyncio.sleep(seconds)


async def main() -> None:
    setup_logging("background")
    s = get_settings()
    dialer = DialService()
    analyzer = PostCallAnalyzer()
    await asyncio.gather(
        _every(s.DIALER_POLL_SECONDS, "dialer", dialer.tick),
        _every(300, "stale dial jobs", dialer.release_stale_jobs),
        _every(10, "CRM webhooks", deliver_pending),
        _every(15, "post-call summaries", analyzer.run_pending),
        _every(3600, "recording retention", delete_expired_recordings),
    )


if __name__ == "__main__":
    asyncio.run(main())
