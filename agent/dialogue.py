"""Transport-independent dialogue engine.

The same engine runs phone calls (LiveKit worker), the browser test console and
text simulations. Per caller turn:

1. pre_turn(): lexicon fix, backchannel filter, fast-path guard (scripted,
   pre-rendered responses, LLM skipped), stage update, observer launched async.
2. stream_reply(): stage prompt -> LLM with server-validated tools -> sentence
   buffer -> output guard per sentence -> sentences yielded to TTS.
3. pending_action: end_call / transfer for the transport to execute after playout.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from agent.callback_time import parse_callback_time
from agent.knowledge import CampaignKnowledge
from agent.observer import CallObserver, CallState
from agent.prompts import build_messages
from agent.stages import SalesStage, StageManager, extract_signals
from agent.tools import TOOL_SCHEMAS, CallIdentity, CallTools, PendingAction, record_opt_out
from guardrails.authorization import AuthorizationContext
from guardrails.backchannel_filter import BackchannelFilter
from guardrails.fast_path_guard import FastPathGuard
from guardrails.output_guard import OutputGuard, SentenceBuffer
from llm.client import LLMClient, LLMUnavailableError, Usage
from observability import metrics as m
from speech.lexicon import apply_lexicon
from speech.phrase_cache import PreRenderedPhraseCache as P

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3
MAX_SENTENCES_PER_REPLY = 3  # prompt asks for 2; the cap is a safety net that keeps the closing question


@dataclass
class Utterance:
    """Something the agent says: a pre-rendered phrase (phrase_id) or live TTS text."""
    text: str
    phrase_id: Optional[str] = None
    kind: str = "sentence"  # sentence | filler | scripted | guard_replacement
    guard_reasons: List[str] = field(default_factory=list)


@dataclass
class ScriptedTurn:
    """Fast-path result: speak these utterances, skip the LLM."""
    utterances: List[Utterance]
    intent: Optional[str] = None
    ignore: bool = False  # backchannel: say nothing


class DialogueEngine:
    def __init__(
        self,
        identity: CallIdentity,
        knowledge: CampaignKnowledge,
        llm: Optional[LLMClient],
        *,
        observer_llm: Optional[LLMClient] = None,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.identity = identity
        self.knowledge = knowledge
        self.llm = llm
        self.phrases = P()
        self.auth = AuthorizationContext()
        self.tools = CallTools(identity, knowledge, self.auth)
        self.stage = StageManager()
        self.state = CallState(identity.lead_id, identity.phone_number, identity.campaign_id)
        self.observer = CallObserver(self.state, observer_llm, on_intent=self._on_late_intent)
        self.guard = OutputGuard(self.phrases.get_text(P.SAFE_GUARD_REPLACEMENT))
        self.fast_path = FastPathGuard()
        self.backchannel = BackchannelFilter()
        self._emit = on_event or (lambda kind, data: None)

        self.history: List[Dict[str, Any]] = []
        self.transcript: List[Dict[str, Any]] = []
        self.usage = Usage()
        self.turns = 0
        self.guard_replacements = 0
        self.awaiting_callback_time = False
        self.opening_denials = 0
        self.disclosure_played = False
        self.last_reply = ""
        self.last_turn_metrics: Dict[str, Any] = {}
        self.late_intent_handler: Optional[Callable[[str], Awaitable[None]]] = None
        self._observer_tasks: set = set()
        self._handled_intents: set = set()

    # ------------------------------------------------------------------
    @property
    def pending_action(self) -> Optional[PendingAction]:
        return self.tools.pending_action

    @property
    def outcome(self) -> Optional[str]:
        return self.tools.outcome

    def _end(self, outcome: str, **detail) -> None:
        self.tools.outcome = self.tools.outcome or outcome
        self.tools.pending_action = PendingAction("end_call", self.tools.outcome, detail)

    def _say(self, speaker: str, text: str, **extra) -> None:
        self.transcript.append({"speaker": speaker, "text": text, "stage": self.stage.current_stage.value,
                                "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), **extra})

    def _phrase(self, phrase_id: str, kind: str = "scripted") -> Utterance:
        return Utterance(self.phrases.get_text(phrase_id), phrase_id=phrase_id, kind=kind)

    # ------------------------------------------------------------------
    def opening(self) -> List[Utterance]:
        """AI + recording disclosure (pre-rendered) and the right-person check."""
        disclosure = self._phrase(P.DISCLOSURE)
        ask = Utterance(f"Kya meri baat {self.identity.customer_name} ji se ho rahi hai?")
        self.disclosure_played = True
        for u in (disclosure, ask):
            self._say("agent", u.text, kind=u.kind)
        self.history.append({"role": "assistant", "content": f"{disclosure.text} {ask.text}"})
        self.last_reply = ask.text
        self._emit("disclosure", {"text": disclosure.text})
        return [disclosure, ask]

    # ------------------------------------------------------------------
    async def pre_turn(self, raw_text: str, *, agent_was_speaking: bool = False) -> Optional[ScriptedTurn]:
        """Screens a final caller transcript. Returns a ScriptedTurn to skip the LLM, else None."""
        text = apply_lexicon(raw_text.strip())
        if not text:
            return ScriptedTurn([], ignore=True)
        if agent_was_speaking and self.backchannel.is_backchannel(text):
            self._emit("backchannel", {"text": text})
            return ScriptedTurn([], ignore=True)

        self.turns += 1
        self._say("customer", text)
        self.history.append({"role": "user", "content": text})
        self._emit("transcript", {"text": text, "turn": self.turns})

        fast = self.fast_path.evaluate(text)
        if fast.triggered:
            m.record_fast_path(fast.intent)
            self._emit("fast_path", {"intent": fast.intent})
            return await self._handle_intent(fast.intent, text)

        if self.awaiting_callback_time:
            return await self._schedule_from_text(text, default="kal subah")

        sig = extract_signals(text)
        if self.stage.current_stage == SalesStage.OPENING and sig.permission_denied:
            self.opening_denials += 1
            self._end("not_interested")
            return self._scripted([self._phrase(P.PERMISSION_DENIED_CLOSE)], "permission_denied")

        reason = self.stage.apply(sig)
        self.observer.apply_signals(sig)
        self.state.current_stage = self.stage.current_stage.value
        if reason:
            self._emit("stage_change", {"to": self.stage.current_stage.value, "reason": reason})

        task = asyncio.create_task(self.observer.observe_turn(text, self.last_reply))
        self._observer_tasks.add(task)
        task.add_done_callback(self._observer_tasks.discard)
        return None

    def _scripted(self, utterances: List[Utterance], intent: Optional[str]) -> ScriptedTurn:
        for u in utterances:
            self._say("agent", u.text, kind=u.kind)
        self.history.append({"role": "assistant", "content": " ".join(u.text for u in utterances)})
        self.last_reply = utterances[-1].text if utterances else ""
        return ScriptedTurn(utterances, intent=intent)

    async def _handle_intent(self, intent: str, text: str) -> ScriptedTurn:
        self._handled_intents.add(intent)
        if intent == "opt_out":
            await record_opt_out(self.identity, channel="voice_fast_path")
            self.auth.mark_action("record_opt_out")
            self._end("opted_out")
            return self._scripted([self._phrase(P.OPT_OUT_CONFIRMED)], intent)
        if intent == "complaint":
            await self._log_complaint(text)
            self._end("complaint")
            return self._scripted([self._phrase(P.COMPLAINT_LOGGED)], intent)
        if intent == "abuse":
            self._end("abusive")
            return self._scripted([self._phrase(P.ABUSE_CLOSE)], intent)
        if intent == "wrong_person":
            self._end("wrong_person")
            return self._scripted([self._phrase(P.WRONG_PERSON)], intent)
        if intent == "transfer_human":
            result = await self.tools.tool_transfer_to_human(reason="customer_requested_human")
            if result.get("transferred"):
                return self._scripted([self._phrase(P.TRANSFER_TO_AGENT)], intent)
            self._end("callback_scheduled")
            return self._scripted([Utterance(result["approved_phrasing"] + " Dhanyawaad.")], intent)
        if intent == "call_later":
            when = parse_callback_time(text, tz_name=self.identity.timezone)
            if when is None:
                self.awaiting_callback_time = True
                return self._scripted([self._phrase(P.ASK_CALLBACK_TIME)], intent)
            return await self._schedule_from_text(text)
        return ScriptedTurn([], ignore=True)

    async def _schedule_from_text(self, text: str, default: Optional[str] = None) -> ScriptedTurn:
        self.awaiting_callback_time = False
        result = await self.tools.tool_schedule_callback(text)
        if not result.get("ok") and default:
            result = await self.tools.tool_schedule_callback(default)
        if not result.get("ok"):
            self._end("callback_requested")
            return self._scripted([self._phrase(P.GOODBYE)], "call_later")
        return self._scripted([Utterance(result["approved_phrasing"] + " Dhanyawaad.")], "call_later")

    async def _log_complaint(self, text: str) -> None:
        try:
            from database.models import Complaint
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                db.add(Complaint(phone_number=self.identity.phone_number, lead_id=self.identity.lead_id,
                                 call_id=self.identity.call_id, channel="voice", description=text))
                await db.commit()
        except Exception as e:
            logger.error(f"Complaint not persisted: {e!r}")

    async def _on_late_intent(self, intent: str, confidence: float) -> None:
        """Observer caught a critical intent the regex missed; let the transport interrupt."""
        if intent in self._handled_intents or self.pending_action is not None:
            return
        if intent not in {"opt_out", "transfer_human", "abuse", "complaint", "wrong_person"}:
            return
        logger.warning(f"Observer late intent '{intent}' ({confidence:.2f}) on {self.identity.call_id}")
        self._emit("late_intent", {"intent": intent, "confidence": confidence})
        if self.late_intent_handler:
            await self.late_intent_handler(intent)

    async def handle_late_intent(self, intent: str) -> ScriptedTurn:
        last_customer = next((t["text"] for t in reversed(self.transcript) if t["speaker"] == "customer"), "")
        return await self._handle_intent(intent, last_customer)

    # ------------------------------------------------------------------
    def _objection_context(self) -> tuple:
        if self.stage.current_stage != SalesStage.OBJECTION_HANDLING or not self.state.identified_objections:
            return None, None
        kind = self.state.identified_objections[-1]
        answer = self.knowledge.objection_playbooks.get(kind)
        if answer:
            self.auth.authorize_text(answer)  # approved playbook values may be spoken
        return answer, kind

    async def stream_reply(self) -> AsyncIterator[Utterance]:
        """Generates the reply sentence by sentence; every sentence passes the output guard first."""
        if self.llm is None:
            yield self._phrase(P.NETWORK_ISSUE_FALLBACK)
            self._end("failed", reason="no_llm")
            return

        stage_prompt = self.knowledge.stage_prompts.get(self.stage.current_stage.value, "")
        self.auth.authorize_text(stage_prompt)
        answer, kind = self._objection_context()
        messages = build_messages(self.knowledge, self.stage, self.state, self.identity.customer_name,
                                  self.history, answer, kind)

        started = time.monotonic()
        spoken: List[str] = []
        sentences = 0
        ttft_ms: Optional[float] = None
        filler_played = False
        last_was_replacement = False

        try:
            for _round in range(MAX_TOOL_ROUNDS + 1):
                buffer = SentenceBuffer()
                tool_calls = []
                round_text = ""
                tools = TOOL_SCHEMAS if _round < MAX_TOOL_ROUNDS else None
                async for ev in self.llm.stream_chat(messages, tools=tools, purpose="reply"):
                    if ev.type == "stall":
                        if not filler_played:
                            filler_played = True
                            yield self._phrase(P.LLM_FILLER, kind="filler")
                        continue
                    if ev.ttft_ms is not None and ttft_ms is None:
                        ttft_ms = ev.ttft_ms
                    if ev.type == "text":
                        round_text += ev.text
                        for sentence in buffer.push(ev.text):
                            out = self._guarded(sentence, last_was_replacement)
                            if out and sentences < MAX_SENTENCES_PER_REPLY:
                                sentences += 1
                                last_was_replacement = out.kind == "guard_replacement"
                                spoken.append(out.text)
                                yield out
                    elif ev.type == "tool_calls":
                        tool_calls = ev.tool_calls
                    elif ev.type == "done" and ev.usage:
                        self.usage.add(ev.usage)
                for sentence in buffer.flush():
                    out = self._guarded(sentence, last_was_replacement)
                    if out and sentences < MAX_SENTENCES_PER_REPLY:
                        sentences += 1
                        last_was_replacement = out.kind == "guard_replacement"
                        spoken.append(out.text)
                        yield out

                if not tool_calls:
                    break
                messages.append({"role": "assistant", "content": round_text or None, "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments or "{}"}}
                    for tc in tool_calls]})
                for tc in tool_calls:
                    args = tc.parsed_arguments()
                    result = await self.tools.execute(tc.name, args)
                    self._emit("tool_call", {"tool": tc.name, "arguments": args, "result": result})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
        except LLMUnavailableError as e:
            logger.error(f"LLM unavailable mid-call {self.identity.call_id}: {e}")
            m.record_vendor_error("llm", "unavailable")
            fallback = self._phrase(P.NETWORK_ISSUE_FALLBACK)
            spoken.append(fallback.text)
            yield fallback
            await self.tools.tool_schedule_callback("2 ghante baad")
            self._end("dropped", reason="llm_unavailable")

        reply = " ".join(spoken).strip()
        if not reply and not self.pending_action:
            ack = self._phrase(P.ACK_SHORT)
            reply = ack.text
            yield ack
        self.history.append({"role": "assistant", "content": reply})
        self._say("agent", reply)
        self.last_reply = reply
        self.stage.note_agent_reply(reply)
        if self.outcome in ("payment_link_sent", "demo_booked") and self.stage.current_stage != SalesStage.COMPLETED:
            self.stage.transition_to(SalesStage.COMPLETED, f"Action confirmed: {self.outcome}")
        self.last_turn_metrics = {"llm_ttft_ms": ttft_ms, "reply_ms": (time.monotonic() - started) * 1000,
                                  "sentences": sentences}
        self._emit("agent_reply", {"text": reply, **self.last_turn_metrics})

    def _guarded(self, sentence: str, last_was_replacement: bool) -> Optional[Utterance]:
        result = self.guard.verify_sentence(sentence, context=self.auth)
        if result.passed:
            return Utterance(sentence)
        self.guard_replacements += 1
        for rule in result.rules:
            m.record_guard_intervention(rule)
        self._emit("guard_intervention", {"original": sentence, "reasons": result.reasons, "rules": result.rules})
        if last_was_replacement:
            return None  # never repeat the safe line back to back
        return Utterance(result.sanitized_sentence, phrase_id=P.SAFE_GUARD_REPLACEMENT,
                         kind="guard_replacement", guard_reasons=result.reasons)

    # ------------------------------------------------------------------
    async def drain_observer(self, timeout: float = 3.0) -> None:
        if self._observer_tasks:
            await asyncio.wait(list(self._observer_tasks), timeout=timeout)

    def summary(self) -> Dict[str, Any]:
        total_usage = Usage()
        total_usage.add(self.usage)
        total_usage.add(self.observer.usage)
        return {
            "outcome": self.outcome,
            "stage_reached": self.stage.current_stage.value,
            "turns": self.turns,
            "guard_replacements": self.guard_replacements,
            "disclosure_played": self.disclosure_played,
            "transcript": self.transcript,
            "call_state": self.state.to_dict(),
            "stage_history": self.stage.history,
            "tool_log": self.tools.tool_log,
            "usage": total_usage,
        }
