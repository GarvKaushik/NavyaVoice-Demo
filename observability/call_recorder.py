"""Per-call persistence: call record, batched events (every 30 s and at call end),
disclosure audit, cost ledger, lead outcome, retry scheduling and the CRM outbox.

Nothing here runs on the per-turn audio path synchronously: events are buffered
in memory and flushed by a background task. Every DB step is best-effort so a
database outage never breaks a live call (the opt-out path also writes Redis).
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from config.settings import get_settings
from observability.metrics import CostCalculator

logger = logging.getLogger(__name__)

# Retry policy per outcome: delay before the next attempt (None = no retry)
RETRY_DELAYS = {
    "busy": timedelta(minutes=30),
    "no_answer": timedelta(hours=2),
    "voicemail": timedelta(hours=4),
    "dropped": timedelta(minutes=30),
    "failed": timedelta(hours=1),
    "no_response": timedelta(hours=3),
}
FINAL_OUTCOMES = {"opted_out", "wrong_person", "abusive", "complaint", "not_interested", "invalid_number"}
CRM_OUTCOMES = {"interested", "payment_link_sent", "demo_booked", "transferred", "not_interested", "opted_out",
                "callback_scheduled"}


class CallRecorder:
    def __init__(self, identity, console: bool = False):
        self.identity = identity
        self.console = console
        self.settings = get_settings()
        self._events: List[Dict[str, Any]] = []
        self._turn = 0
        self.stt_seconds = 0.0
        self.tts_chars = 0
        self.tts_cached_chars = 0
        self.p50: Optional[float] = None
        self.p95: Optional[float] = None
        self.false_interruptions = 0
        self.talk_share: Optional[float] = None
        self.error: Optional[str] = None
        self.finalized = False
        self._flusher: Optional[asyncio.Task] = None
        self._db_ok = True

    # ------------------------------------------------------------------
    def add_event(self, kind: str, data: Dict[str, Any]) -> None:
        if kind == "transcript":
            self._turn = data.get("turn", self._turn + 1)
        self._events.append({"event_type": kind, "turn_number": self._turn, "details": data})

    def add_stt_seconds(self, seconds: float) -> None:
        self.stt_seconds += max(seconds or 0.0, 0.0)

    def add_tts_chars(self, n: int, cached: bool) -> None:
        if cached:
            self.tts_cached_chars += n
        else:
            self.tts_chars += n

    def mark_disclosure(self) -> None:
        self.add_event("disclosure", {"played": True})
        asyncio.create_task(self._write_disclosure())

    # ------------------------------------------------------------------
    async def start(self, script_version: str, caller_id: str) -> None:
        try:
            from database.models import CallRecord, utcnow
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                existing = await db.get(CallRecord, self.identity.call_id)
                if existing is None:
                    existing = CallRecord(call_id=self.identity.call_id, lead_id=self.identity.lead_id,
                                          campaign_id=self.identity.campaign_id, caller_id_used=caller_id,
                                          direction="console" if self.console else "outbound")
                    db.add(existing)
                existing.status = "in_progress"
                existing.script_version = script_version
                existing.voice_id = self.settings.BULBUL_TTS_VOICE_ID
                existing.answered_at = utcnow()
                await db.commit()
        except Exception as e:
            self._db_ok = False
            logger.warning(f"Call record not created (DB unavailable?): {e!r}")
        self._flusher = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        while not self.finalized:
            await asyncio.sleep(self.settings.CALL_EVENT_FLUSH_SECONDS)
            await self.flush()

    async def flush(self) -> None:
        if not self._db_ok:
            self._events.clear()
            return
        if not self._events:
            return
        batch, self._events = self._events, []
        try:
            from database.models import CallEvent
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                for ev in batch:
                    details = ev["details"]
                    db.add(CallEvent(call_id=self.identity.call_id, turn_number=ev["turn_number"],
                                     event_type=ev["event_type"][:32], details=_jsonable(details),
                                     latency_llm_ttft_ms=details.get("llm_ttft_ms") or 0.0,
                                     total_turn_latency_ms=details.get("voice_to_voice_ms") or 0.0))
                await db.commit()
        except Exception as e:
            logger.warning(f"Event flush failed ({len(batch)} events): {e!r}")

    async def _write_disclosure(self) -> None:
        try:
            from database.models import ConsentRecord, utcnow
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                db.add(ConsentRecord(lead_id=self.identity.lead_id, phone_number=self.identity.phone_number,
                                     call_id=self.identity.call_id, event="disclosure_played",
                                     consent_source="call", disclosure_played=True, disclosure_played_at=utcnow()))
                await db.commit()
        except Exception as e:
            logger.debug(f"Disclosure audit not written: {e!r}")

    # ------------------------------------------------------------------
    async def finalize(self, engine, duration_seconds: float) -> None:
        if self.finalized:
            return
        self.finalized = True
        summary = engine.summary()
        usage = summary["usage"]
        outcome = summary["outcome"] or ("interested" if summary["stage_reached"] in ("PITCH", "CLOSE_NEXT_STEP") else "not_interested")
        cost = CostCalculator.compute_call_cost(
            duration_seconds=int(duration_seconds), stt_audio_seconds=self.stt_seconds,
            tts_character_count=self.tts_chars, llm_input_tokens=usage.input_tokens,
            llm_cached_tokens=usage.cached_tokens, llm_output_tokens=usage.output_tokens,
        )
        logger.info(
            f"CALL END {self.identity.call_id}: outcome={outcome} stage={summary['stage_reached']} "
            f"turns={summary['turns']} duration={duration_seconds:.0f}s cost=₹{cost.total_inr} "
            f"(₹{cost.per_connected_minute_inr}/min) p50={self.p50 and round(self.p50)}ms "
            f"tts_cached={self.tts_cached_chars}/{self.tts_cached_chars + self.tts_chars} chars"
        )
        await self.flush()
        if not self._db_ok:
            return
        try:
            from database.models import CallRecord, CRMOutbox, utcnow
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                rec = await db.get(CallRecord, self.identity.call_id)
                if rec is None:
                    return
                rec.status = "ended"
                rec.end_time = utcnow()
                rec.duration_seconds = int(duration_seconds)
                rec.billed_minutes = cost.billed_minutes
                rec.outcome = outcome
                rec.stage_reached = summary["stage_reached"]
                rec.disclosure_played = summary["disclosure_played"]
                rec.cost_telephony_inr = 0.0 if self.console else cost.telephony_inr
                rec.cost_stt_inr, rec.cost_tts_inr, rec.cost_llm_inr = cost.stt_inr, cost.tts_inr, cost.llm_inr
                rec.cost_orchestration_inr = cost.orchestration_inr
                rec.cost_infra_inr, rec.cost_contingency_inr = cost.infra_inr, cost.contingency_inr
                rec.cost_total_inr = cost.total_inr - (cost.telephony_inr if self.console else 0.0)
                rec.stt_audio_seconds, rec.tts_characters = self.stt_seconds, self.tts_chars
                rec.tts_cached_characters = self.tts_cached_chars
                rec.llm_input_tokens, rec.llm_cached_tokens = usage.input_tokens, usage.cached_tokens
                rec.llm_output_tokens = usage.output_tokens
                rec.turn_count = summary["turns"]
                rec.guard_replacements = summary["guard_replacements"]
                rec.false_interruptions = self.false_interruptions
                rec.agent_talk_share = self.talk_share
                rec.p50_latency_ms, rec.p95_latency_ms = self.p50, self.p95
                rec.transcript = _jsonable(summary["transcript"])
                rec.call_state = _jsonable({**summary["call_state"], "tool_log": summary["tool_log"],
                                            "stage_history": summary["stage_history"]})
                rec.error = self.error
                if outcome in CRM_OUTCOMES and not self.console:
                    db.add(CRMOutbox(event_type=f"call.{outcome}", payload=_jsonable({
                        "call_id": self.identity.call_id, "lead_id": self.identity.lead_id,
                        "campaign_id": self.identity.campaign_id, "outcome": outcome,
                        "stage_reached": summary["stage_reached"], "call_state": summary["call_state"]})))
                await db.commit()
            if not self.console:
                await self._update_lead(outcome)
        except Exception as e:
            logger.error(f"Call finalize write failed: {e!r}")

    async def finalize_unanswered(self, outcome: str, error: Optional[str] = None) -> None:
        self.finalized = True
        self.error = error
        try:
            from database.models import CallRecord, utcnow
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                rec = await db.get(CallRecord, self.identity.call_id)
                if rec:
                    rec.status, rec.outcome, rec.end_time, rec.error = "ended", outcome, utcnow(), error
                    rec.answered_at = None
                    await db.commit()
            await self._update_lead(outcome)
        except Exception as e:
            logger.error(f"Unanswered finalize failed: {e!r}")

    async def _update_lead(self, outcome: str) -> None:
        from sqlalchemy import update
        from database.models import DialJob, Lead, utcnow
        from database.session import AsyncSessionLocal
        from services.redis_service import get_redis_service

        await get_redis_service().release_call_lock(self.identity.phone_number, self.identity.call_id)
        async with AsyncSessionLocal() as db:
            lead = await db.get(Lead, self.identity.lead_id)
            if lead is None:
                return
            lead.last_outcome = outcome
            await db.execute(update(DialJob).where(DialJob.call_id == self.identity.call_id).values(status="done"))
            max_attempts = self.settings.MAX_DIAL_ATTEMPTS_PER_LEAD
            if outcome == "opted_out":
                lead.status = "opted_out"
            elif outcome in FINAL_OUTCOMES or outcome in ("payment_link_sent", "demo_booked", "transferred"):
                lead.status = "done"
            elif outcome == "callback_scheduled":
                lead.status = "pending"  # the callback job already exists
            elif outcome in RETRY_DELAYS and (lead.attempt_count or 0) < max_attempts:
                when = utcnow() + RETRY_DELAYS[outcome]
                lead.status, lead.next_attempt_at = "pending", when
                db.add(DialJob(lead_id=lead.id, campaign_id=self.identity.campaign_id, scheduled_at=when,
                               reason="retry", attempt_number=(lead.attempt_count or 0) + 1))
            else:
                lead.status = "exhausted" if outcome in RETRY_DELAYS else "done"
            await db.commit()


def _jsonable(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str, ensure_ascii=False))
