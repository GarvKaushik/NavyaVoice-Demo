"""Offline Intelligence Worker: Post-Call Analysis using Sarvam 105B.

Operates strictly out-of-band, after a call has ended:
1. Generates a short structured summary and next step for the client's dashboard.
2. Checks the zero-tolerance compliance items that can be read from the call record.
(Note: Nothing generated here ever reaches a live call.)

The client-facing conclusion (Ready, Refused, ...) comes from the recorded outcome,
not from this summary.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from config.settings import get_settings
from database.models import CallRecord, utcnow
from database.session import AsyncSessionLocal
from llm.client import LLMClient, LLMUnavailableError, parse_json_object

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You summarise one Hinglish sales call for the business that made it. Use only what the transcript says.
Return ONLY a JSON object:
{"summary": "2 short English sentences on what happened", "next_step": "one short English sentence, or null",
 "objections": [short English phrases], "sentiment": one of ["positive","neutral","skeptical","hostile"]}"""


class PostCallAnalyzer:
    """Executes offline analysis on completed call transcripts."""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm

    def _client(self) -> LLMClient:
        if self.llm is None:
            self.llm = LLMClient.offline()
        return self.llm

    async def analyze_completed_call(
        self,
        call_id: str,
        transcript_turns: List[Dict[str, Any]],
        final_outcome: str,
        disclosure_played: bool = False,
    ) -> Dict[str, Any]:
        """Returns the summary and compliance checks. Raises LLMUnavailableError to retry later."""
        logger.info(f"Running offline analysis for Call {call_id}")
        text = "\n".join(f"{t.get('speaker', 'agent')}: {t.get('text', '')}" for t in transcript_turns)
        reply, _usage = await self._client().complete(
            [{"role": "system", "content": SUMMARY_PROMPT},
             {"role": "user", "content": json.dumps({"outcome": final_outcome, "transcript": text}, ensure_ascii=False)}],
            max_tokens=300, json_mode=True, purpose="post_call",
            extra_body={"reasoning_effort": None})  # otherwise hidden reasoning uses every token and no text comes back
        data = parse_json_object(reply) or {}
        return {
            "summary": str(data.get("summary") or "No summary available."),
            "next_step": data.get("next_step"),
            "objections": [str(o) for o in (data.get("objections") or [])][:5],
            "sentiment": data.get("sentiment") if data.get("sentiment") in ("positive", "neutral", "skeptical", "hostile") else "neutral",
            "compliance": {"disclosure_played": bool(disclosure_played)},
        }

    async def run_pending(self, limit: int = 10) -> int:
        """Summarises ended calls that have a conversation. Returns how many were done."""
        done = 0
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(CallRecord).where(
                CallRecord.status == "ended", CallRecord.analyzed_at.is_(None), CallRecord.turn_count > 0,
                CallRecord.direction == "outbound"
            ).order_by(CallRecord.start_time).limit(limit).with_for_update(skip_locked=True))).scalars().all()
            for rec in rows:
                try:
                    rec.summary = await self.analyze_completed_call(rec.call_id, rec.transcript or [], rec.outcome,
                                                                    rec.disclosure_played)
                except LLMUnavailableError as e:
                    logger.warning(f"Summary postponed: {e}")
                    break
                rec.analyzed_at = utcnow()
                done += 1
            await db.commit()
        return done
