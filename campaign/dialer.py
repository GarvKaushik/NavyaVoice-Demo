"""Outbound dialer: compliance check, per-number lock, CPS pacing, then LiveKit dispatch.

The dialer never calls the carrier directly. It creates a LiveKit room and
explicitly dispatches the agent with call metadata; the agent worker then dials
the customer through the Plivo SIP trunk (see agent.worker.dial_customer).
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

from livekit import api

from campaign.compliance import ComplianceManager
from config.settings import get_settings
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)
settings = get_settings()


class OutboundDialer:
    """Dispatches and paces outbound campaign dials into LiveKit rooms."""

    def __init__(self):
        self.compliance_mgr = ComplianceManager()
        self.redis_service = get_redis_service()

    async def dial_single_lead(
        self,
        lead_id: str,
        phone_number: str,
        attempt_count: int,
        campaign_id: str = "CAMP-REENGAGE-Q4",
        is_promotional: bool = True,
        customer_name: str = "",
        timezone: Optional[str] = None,
        segment: Optional[str] = None,
        skip_window_check: bool = False,
    ) -> Dict[str, Any]:
        """Validates, locks, paces, and dispatches a single outbound call."""
        can_dial, reason = await self.compliance_mgr.can_dial_lead(
            phone_number=phone_number, attempt_count=attempt_count, is_promotional=is_promotional,
            timezone=timezone, skip_window_check=skip_window_check,
        )
        if not can_dial:
            logger.info(f"Skipping dial for Lead {lead_id} ({phone_number}): {reason}")
            return {"status": "skipped", "reason": reason, "lead_id": lead_id}

        call_id = f"call_{uuid.uuid4().hex[:12]}"
        if not await self.redis_service.acquire_call_lock(phone_number, call_id):
            return {"status": "already_in_call", "lead_id": lead_id}

        if not await self.redis_service.check_and_consume_cps_token(campaign_id):
            await self.redis_service.release_call_lock(phone_number, call_id)
            return {"status": "cps_throttled", "lead_id": lead_id}

        caller_id = self.compliance_mgr.get_caller_id(is_promotional)
        metadata = {
            "call_id": call_id, "lead_id": lead_id, "campaign_id": campaign_id, "phone_number": phone_number,
            "customer_name": customer_name or "ji", "caller_id": caller_id, "timezone": timezone or settings.TIMEZONE,
            "segment": segment,
        }
        try:
            await self._create_call_record(call_id, lead_id, campaign_id, caller_id)
            dispatch_id = await self._dispatch(call_id, metadata)
        except Exception as e:
            await self.redis_service.release_call_lock(phone_number, call_id)
            logger.error(f"Dispatch failed for {lead_id}: {e!r}")
            return {"status": "dispatch_failed", "lead_id": lead_id, "error": str(e)}

        await self._increment_attempts(lead_id)
        return {"status": "dispatched", "call_id": call_id, "lead_id": lead_id, "caller_id": caller_id,
                "dispatch_id": dispatch_id}

    async def _dispatch(self, room: str, metadata: Dict[str, Any]) -> str:
        lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        try:
            await lk.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=60, max_participants=4))
            dispatch = await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
                agent_name=settings.LIVEKIT_AGENT_NAME, room=room, metadata=json.dumps(metadata)))
            logger.info(f"Dispatched agent to room {room} for lead {metadata['lead_id']}")
            return dispatch.id
        finally:
            await lk.aclose()

    async def _create_call_record(self, call_id: str, lead_id: str, campaign_id: str, caller_id: str) -> None:
        try:
            from database.models import CallRecord
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                db.add(CallRecord(call_id=call_id, lead_id=lead_id, campaign_id=campaign_id,
                                  caller_id_used=caller_id, status="dialing"))
                await db.commit()
        except Exception as e:
            logger.warning(f"Call record not pre-created: {e!r}")

    async def _increment_attempts(self, lead_id: str) -> None:
        try:
            from sqlalchemy import update
            from database.models import Lead
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await db.execute(update(Lead).where(Lead.id == lead_id).values(
                    attempt_count=Lead.attempt_count + 1, status="in_progress"))
                await db.commit()
        except Exception as e:
            logger.warning(f"Attempt count not updated: {e!r}")
