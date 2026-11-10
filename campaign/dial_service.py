"""Dialer service: turns due dial jobs into calls, fairly across clients.

Campaign starts, retries and callbacks are all rows in dial_jobs. Each tick claims
jobs with FOR UPDATE SKIP LOCKED (so several dialers can run), one per client per
pass, within three limits: the platform's concurrent calls, each client's concurrent
call cap, and the carrier's calls per second. A client out of minutes simply has
its jobs wait, and they resume after a top-up.
"""

import logging
import os
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pytz
from sqlalchemy import func, select, update

from campaign.dialer import OutboundDialer
from config.settings import get_settings
from database.models import CallRecord, Campaign, Client, DialJob, Lead, utcnow
from database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

ACTIVE_CALL_WINDOW = timedelta(minutes=30)  # a call row older than this is a crashed call, not an active one
STALE_JOB_AFTER = timedelta(hours=1)
MAX_CLAIMS_PER_TICK = 50


def next_window_start(tz_name: str, now: Optional[datetime] = None) -> datetime:
    """Naive UTC time of the next opening of the calling window in the lead's time zone."""
    s = get_settings()
    tz = pytz.timezone(tz_name or s.TIMEZONE)
    local = (now or datetime.now(pytz.utc)).astimezone(tz)
    start = local.replace(hour=s.COMPLIANCE_CALLING_START_HOUR, minute=0, second=0, microsecond=0)
    if local >= start:
        start += timedelta(days=1)
    return start.astimezone(pytz.utc).replace(tzinfo=None)


class DialService:
    def __init__(self, dialer: Optional[OutboundDialer] = None):
        self.dialer = dialer or OutboundDialer()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.settings = get_settings()
        self._offset = 0

    async def _active_calls(self) -> Dict[str, int]:
        cutoff = utcnow() - ACTIVE_CALL_WINDOW
        async with AsyncSessionLocal() as db:
            rows = await db.execute(select(CallRecord.client_id, func.count()).where(
                CallRecord.status.in_(["dialing", "in_progress"]), CallRecord.direction == "outbound",
                CallRecord.start_time > cutoff).group_by(CallRecord.client_id))
            return {client_id: count for client_id, count in rows.all()}

    async def _eligible_clients(self) -> Dict[str, int]:
        """Clients with minutes left, and their concurrent call caps."""
        async with AsyncSessionLocal() as db:
            rows = await db.execute(select(Client.id, Client.max_concurrent_calls).where(
                Client.status == "active", Client.balance_seconds > 0).order_by(Client.id))
            return {client_id: cap or 0 for client_id, cap in rows.all()}

    async def _claim(self, client_id: str) -> Optional[tuple]:
        now = utcnow()
        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(DialJob).join(Campaign, Campaign.id == DialJob.campaign_id).where(
                    DialJob.client_id == client_id, DialJob.status == "queued", DialJob.scheduled_at <= now,
                    Campaign.status == "active")
                .order_by(DialJob.scheduled_at).limit(1).with_for_update(skip_locked=True, of=DialJob)
            )).scalars().first()
            if job is None:
                return None
            job.status, job.locked_by, job.locked_at = "dialing", self.worker_id, now
            lead = await db.get(Lead, job.lead_id)
            campaign = await db.get(Campaign, job.campaign_id)
            await db.commit()
            return job.id, job.reason, lead, bool(campaign.is_promotional)

    async def _finish_job(self, job_id: int, **values) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(update(DialJob).where(DialJob.id == job_id).values(**values))
            await db.commit()

    async def _run_job(self, job_id: int, reason: str, lead: Optional[Lead], is_promotional: bool) -> bool:
        """Dials one claimed job. Returns True if a call was dispatched."""
        if lead is None or lead.status == "opted_out":
            await self._finish_job(job_id, status="cancelled", last_error="lead unavailable or opted out")
            return False
        within, _ = self.dialer.compliance_mgr.is_within_calling_window(lead.timezone)
        if not within:
            await self._finish_job(job_id, status="queued", scheduled_at=next_window_start(lead.timezone))
            return False

        # A callback the customer asked for is not another outreach attempt
        attempts = 0 if reason == "callback" else (lead.attempt_count or 0)
        result = await self.dialer.dial_single_lead(
            lead.id, lead.phone_number, attempts, lead.campaign_id or "", is_promotional, lead.full_name,
            lead.timezone, lead.segment, client_id=lead.client_id)
        status = result["status"]
        if status == "dispatched":
            await self._finish_job(job_id, call_id=result["call_id"])
            return True
        if status in ("blocked", "cps_throttled"):
            await self._finish_job(job_id, status="queued", last_error=result.get("reason") or status)
        elif status == "already_in_call":
            await self._finish_job(job_id, status="queued", scheduled_at=utcnow() + timedelta(minutes=5))
        elif status == "dispatch_failed":
            await self._finish_job(job_id, status="queued", scheduled_at=utcnow() + timedelta(minutes=1),
                                   last_error=(result.get("error") or "")[:500])
        else:  # skipped: opted out, attempts used up or no consent
            await self._finish_job(job_id, status="cancelled", last_error=(result.get("reason") or status)[:500])
        return False

    async def tick(self) -> int:
        """One round of dialing. Returns how many calls were dispatched."""
        active = await self._active_calls()
        free = self.settings.DIALER_MAX_CONCURRENT_CALLS - sum(active.values())
        budget = min(free, self.settings.PLIVO_MAX_CALLS_PER_SECOND)
        if budget <= 0:
            return 0
        caps = await self._eligible_clients()
        room = {c: cap - active.get(c, 0) for c, cap in caps.items() if cap - active.get(c, 0) > 0}
        order: List[str] = list(room)
        if not order:
            return 0
        self._offset = (self._offset + 1) % len(order)
        order = order[self._offset:] + order[:self._offset]  # no client always goes first

        dispatched, claims = 0, 0
        while budget > 0 and order and claims < MAX_CLAIMS_PER_TICK:
            for client_id in list(order):
                if budget <= 0 or claims >= MAX_CLAIMS_PER_TICK:
                    break
                claimed = await self._claim(client_id)
                if claimed is None:
                    order.remove(client_id)
                    continue
                claims += 1
                if await self._run_job(*claimed):
                    dispatched += 1
                    budget -= 1
                    room[client_id] -= 1
                    if room[client_id] <= 0:
                        order.remove(client_id)
        return dispatched

    async def release_stale_jobs(self) -> int:
        """Jobs left in 'dialing' by a crashed dialer or worker stop blocking nothing, but are closed out."""
        async with AsyncSessionLocal() as db:
            res = await db.execute(update(DialJob).where(
                DialJob.status == "dialing", DialJob.locked_at < utcnow() - STALE_JOB_AFTER
            ).values(status="failed", last_error="no result from the call worker"))
            await db.commit()
            return res.rowcount or 0
