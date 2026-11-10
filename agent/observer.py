"""Call Observer: asynchronous, non-blocking conversational state extractor.

After each caller turn, a cheap structured-output LLM call updates the call
state with needs, objections, buying signals, budget hints, preferences and
features discussed. It never blocks the reply. It also classifies critical
intents (opt-out, human request, ...) as a second check behind the regex
fast path; a late hit lets the transport interrupt the agent.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, List, Optional

from agent.stages import TurnSignals
from config.settings import get_settings
from llm.client import LLMClient, LLMUnavailableError, Usage, parse_json_object

logger = logging.getLogger(__name__)

CRITICAL_INTENTS = {"opt_out", "transfer_human", "wrong_person", "abuse", "call_later", "complaint"}


@dataclass
class CallState:
    """In-memory active call state maintained on the worker pod."""
    lead_id: str
    phone_number: str
    campaign_id: str
    current_stage: str = "OPENING"
    identified_needs: List[str] = field(default_factory=list)
    identified_objections: List[str] = field(default_factory=list)
    buying_signals: List[str] = field(default_factory=list)
    budget_hints: Optional[str] = None
    preferences: List[str] = field(default_factory=list)
    features_discussed: List[str] = field(default_factory=list)
    turn_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def _add(self, bucket: List[str], items, limit: int = 6) -> None:
        if isinstance(items, str):
            items = [items]
        for item in items or []:
            item = str(item).strip()
            if item and item.lower() not in {x.lower() for x in bucket}:
                bucket.append(item)
        del bucket[:-limit]  # keep only the most recent few


OBSERVER_PROMPT = """You analyse one turn of a Hinglish sales call for a CRM product. Return ONLY a JSON object:
{"needs": [short English phrases], "objection": one of ["price","timing","trust","competitor","need",null],
 "buying_signals": [short phrases], "budget_hint": string or null, "preferences": [short phrases],
 "intent": one of ["none","opt_out","call_later","wrong_person","abuse","transfer_human","complaint"],
 "intent_confidence": number 0-1, "small_talk": true/false}
needs = the customer's business problems only (e.g. "leads get missed"), never prices, discounts or requests.
Return empty lists when nothing new was said in THIS turn. Keep each phrase under 6 words.
opt_out = the customer does not want any more calls. Be conservative: only flag intents clearly stated."""


class CallObserver:
    """Updates CallState from caller turns; heuristics now, LLM refinement in the background."""

    def __init__(self, state: CallState, llm: Optional[LLMClient] = None,
                 on_intent: Optional[Callable[[str, float], Awaitable[None]]] = None):
        self.state = state
        self.llm = llm if (llm is not None and get_settings().OBSERVER_LLM_ENABLED) else None
        self.on_intent = on_intent
        self.usage = Usage()

    def apply_signals(self, sig: TurnSignals) -> None:
        """Immediate keyword-derived update (runs inline, microseconds)."""
        self.state.turn_count += 1
        self.state._add(self.state.identified_needs, sig.needs)
        if sig.objection:
            self.state._add(self.state.identified_objections, [sig.objection])
        if sig.buying_signal:
            self.state._add(self.state.buying_signals, ["customer showed buying interest"])

    async def observe_turn(self, caller_transcript: str, last_agent_reply: str = "") -> Optional[dict]:
        """LLM structured extraction. Safe to fire-and-forget."""
        if not self.llm or not caller_transcript.strip():
            return None
        s = get_settings()
        messages = [
            {"role": "system", "content": OBSERVER_PROMPT},
            {"role": "user", "content": json.dumps({"agent_said": last_agent_reply, "customer_said": caller_transcript},
                                                   ensure_ascii=False)},
        ]
        extra = {"model": s.OBSERVER_LLM_MODEL} if s.OBSERVER_LLM_MODEL else None
        try:
            text, usage = await self.llm.complete(messages, max_tokens=200, json_mode=True,
                                                  extra_body=extra, purpose="observer")
        except LLMUnavailableError as e:
            logger.debug(f"Observer skipped: {e}")
            return None
        self.usage.add(usage)
        data = parse_json_object(text)
        if not data:
            return None
        self.state._add(self.state.identified_needs, data.get("needs"))
        if data.get("objection") in {"price", "timing", "trust", "competitor", "need"}:
            self.state._add(self.state.identified_objections, [data["objection"]])
        self.state._add(self.state.buying_signals, data.get("buying_signals"))
        self.state._add(self.state.preferences, data.get("preferences"))
        if data.get("budget_hint"):
            self.state.budget_hints = str(data["budget_hint"])[:120]

        intent = data.get("intent")
        try:
            confidence = float(data.get("intent_confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if intent in CRITICAL_INTENTS and confidence >= 0.8 and self.on_intent:
            await self.on_intent(intent, confidence)
        return data
