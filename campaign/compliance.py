"""Compliance Manager: TRAI TCCCPR and DPDP checks before every dial.

1. Calling window in the lead's own time zone (configure after counsel confirms the window).
2. DND / opt-out scrub: Redis cache, the permanent Postgres DND registry, and DLT scrub freshness.
3. The client's assigned caller ID (outside production, the configured 140/160-series numbers).
4. Attempt cap per lead.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

import pytz

from config.settings import get_settings
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)
settings = get_settings()


class ComplianceManager:
    """Validates outbound dial eligibility against Indian regulatory frameworks."""

    def __init__(self):
        self.redis_service = get_redis_service()

    def is_within_calling_window(self, timezone: Optional[str] = None, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """Checks the permitted calling hours in the lead's time zone."""
        tz = pytz.timezone(timezone or settings.TIMEZONE)
        local = (now or datetime.now(pytz.utc)).astimezone(tz)
        start, end = settings.COMPLIANCE_CALLING_START_HOUR, settings.COMPLIANCE_CALLING_END_HOUR
        if local.hour < start:
            return False, f"Before permitted hours: {local:%H:%M} {tz.zone} (window {start}:00-{end}:00)"
        if local.hour >= end:
            return False, f"After permitted hours: {local:%H:%M} {tz.zone} (window {start}:00-{end}:00)"
        return True, "Within legal calling window"

    async def can_dial_lead(
        self,
        phone_number: str,
        attempt_count: int,
        client_id: str,
        is_promotional: bool = True,
        timezone: Optional[str] = None,
        skip_window_check: bool = False,
    ) -> Tuple[bool, str]:
        """Must return True before any call is placed."""
        if not skip_window_check:
            within_window, reason = self.is_within_calling_window(timezone)
            if not within_window:
                return False, reason

        if attempt_count >= settings.MAX_DIAL_ATTEMPTS_PER_LEAD:
            return False, f"Max outreach attempt limit ({settings.MAX_DIAL_ATTEMPTS_PER_LEAD}) reached"

        if await self.redis_service.is_dnd_or_opted_out(phone_number, client_id):
            return False, "Lead previously opted out of this client's calls"

        db_check = await self._database_check(phone_number, client_id, is_promotional)
        if db_check is not None:
            return False, db_check
        return True, "Eligible to dial"

    async def _database_check(self, phone_number: str, client_id: str, is_promotional: bool) -> Optional[str]:
        """Client and national DND entries, consent on file, and DLT scrub freshness."""
        try:
            from sqlalchemy import select
            from database.models import DNDEntry, GLOBAL_DND_CLIENT, Lead, utcnow
            from database.session import get_read_sessionmaker

            async with get_read_sessionmaker()() as db:
                if await db.get(DNDEntry, (client_id, phone_number)):
                    return "Number opted out of this client's calls"
                if await db.get(DNDEntry, (GLOBAL_DND_CLIENT, phone_number)):
                    return "Number is on the national DND registry"
                lead = (await db.execute(select(Lead).where(Lead.client_id == client_id,
                                                            Lead.phone_number == phone_number))).scalar()
                if lead is None:
                    return None
                if lead.is_dnd or lead.dlt_registered_dnd:
                    return "Lead is DND (opt-out or DLT registry)"
                if not lead.consent_source:
                    return "No consent source recorded for this lead"
                if is_promotional and settings.DLT_SCRUB_PROVIDER_URL:
                    max_age = timedelta(hours=settings.DLT_SCRUB_MAX_AGE_HOURS)
                    if lead.dlt_scrubbed_at is None or utcnow() - lead.dlt_scrubbed_at > max_age:
                        return "DLT scrub missing or stale; re-scrub before dialing"
        except Exception as e:
            if settings.is_production:
                return f"Compliance database unavailable: {e!r}"
            logger.warning(f"Compliance DB check skipped (development): {e!r}")
        return None

    def get_caller_id(self, is_promotional: bool = True) -> str:
        """Returns the 140-series promotional or 160-series transactional caller ID."""
        if is_promotional:
            return settings.PLIVO_CALLER_ID_PROMOTIONAL
        return settings.PLIVO_CALLER_ID_TRANSACTIONAL or settings.PLIVO_CALLER_ID_PROMOTIONAL
