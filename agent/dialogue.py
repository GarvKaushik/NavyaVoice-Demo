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
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from agent.callback_time import parse_callback_time
from agent.knowledge import CampaignKnowledge
from agent.observer import CallObserver, CallState
from agent.prompts import build_messages
from agent.stages import CallStep, SalesStage, StageManager, extract_signals, link_consent, opt_out_clarity
from agent.tools import CONFIRMED_OUTCOMES, CallIdentity, CallTools, PendingAction, record_opt_out, tool_schemas_for
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
MAX_REPLY_WORDS = 25  # beyond this, only a closing question may still be spoken
MAX_REPLY_WORDS_WITH_QUESTION = 36
MAX_SENTENCES_PER_REPLY = 3  # the prompt asks for 2; the third is only ever a closing question
# A pitch explains the product to someone who has never heard of it, so it gets more room
MAX_PITCH_WORDS = 45
MAX_PITCH_WORDS_WITH_QUESTION = 55
MAX_PITCH_SENTENCES = 4  # the prompt asks for 3; the fourth is only ever a closing question
NAME_EVERY_REPLIES = 3  # replies that must pass before the customer's name may be used again
OPT_OUT_CONFIRM_QUESTION = "Kya main aapka number calling list se hata doon?"


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
        self.phrases = P(voice=knowledge.voice_id, company=knowledge.company_name, persona=knowledge.persona_name,
                         complaint_contact=knowledge.complaint_contact)
        self.auth = AuthorizationContext()
        self.tools = CallTools(identity, knowledge, self.auth)
        self.tools.price_allowed = False  # until the customer asks, objects or wants to buy
        self.tools.amount_heard = False
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
        self.awaiting_opt_out_confirm = False
        self.price_requested = False
        self._replies_since_name = 0  # the opening already used the name
        self._name_in_reply = False
        self._said_amount: Optional[str] = None
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
        self.tools.link_consented = link_consent(text, self.last_reply)
        self.tools.opt_out_allowed = opt_out_clarity(text) != "none"
        self.tools.end_call_allowed = True

        fast = self.fast_path.evaluate(text)
        if fast.triggered:
            m.record_fast_path(fast.intent)
            self._emit("fast_path", {"intent": fast.intent})
            return await self._handle_intent(fast.intent, text)

        if self.awaiting_opt_out_confirm:
            self.awaiting_opt_out_confirm = False
            if extract_signals(text).affirmative:
                return await self._handle_intent("opt_out", text)

        if self.awaiting_callback_time:
            return await self._schedule_from_text(text, default="kal subah")

        sig = extract_signals(text)
        if self.stage.current_stage == SalesStage.OPENING and sig.permission_denied:
            self.opening_denials += 1
            self._end("not_interested")
            return self._scripted([self._phrase(P.PERMISSION_DENIED_CLOSE)], "permission_denied")

        if (self.stage.current_stage == SalesStage.COMPLETED and self.outcome in CONFIRMED_OUTCOMES
                and not (sig.question or sig.objection or sig.buying_signal)):
            self._end(self.outcome)
            return self._scripted([self._phrase(P.GOODBYE)], "goodbye")

        self._apply_answer_to_agent_question(sig)
        self.stage.note_known_needs(self.state.identified_needs)
        reason = self.stage.apply(sig)
        if self.stage.step == CallStep.DECLINED:
            self._end("not_interested")
            return self._scripted([self._phrase(P.PERMISSION_DENIED_CLOSE)], "permission_denied")
        self.tools.end_call_allowed = self.stage.step != CallStep.OFFER_AGAIN  # it still has a question to ask
        if sig.asks_price or sig.wants_price or sig.objection or sig.buying_signal:
            self.tools.price_allowed = True
        self.price_requested = sig.wants_price
        self.observer.apply_signals(sig)
        self.state.current_stage = self.stage.current_stage.value
        if reason:
            self._emit("stage_change", {"to": self.stage.current_stage.value, "reason": reason})

        task = asyncio.create_task(self.observer.observe_turn(text, self.last_reply))
        self._observer_tasks.add(task)
        task.add_done_callback(self._observer_tasks.discard)
        line = self.stage.line_for_step(self.identity.customer_name)
        if line:
            return self._scripted([Utterance(line)], self.stage.step.value)
        return await self._book_chosen_slot(text, sig)

    async def _book_chosen_slot(self, text: str, sig) -> Optional[ScriptedTurn]:
        """A customer who picks an offered slot gets booked by code, so the agent cannot only claim it did."""
        if sig.negative or self.outcome == "demo_booked" or "baje" not in self.last_reply.lower():
            return None
        slot_id = self.tools.slot_from_words(text)
        if slot_id is None:
            return None
        args = {"slot_id": slot_id}
        result = await self.tools.execute("book_demo", args)
        self._emit("tool_call", {"tool": "book_demo", "arguments": args, "result": result})
        if not result.get("ok"):
            return None
        self.price_requested = False
        self.stage.transition_to(SalesStage.COMPLETED, "Action confirmed: demo_booked")
        return self._scripted([Utterance(result["approved_phrasing"])], "book_demo")

    def _apply_answer_to_agent_question(self, sig) -> None:
        """A plain yes accepts what the agent just asked: "follow-up miss hota hai kya?" confirms that need,
        "demo dekhna chahenge?" is a buying signal, "price jaanna chahenge?" asks for the price."""
        question = re.split(r"(?<=[.!?])\s+", self.last_reply.strip())[-1]
        if (self.stage.current_stage == SalesStage.OPENING or not sig.affirmative or not question.endswith("?")
                or not self.stage.last_agent_question_closed):
            return
        asked = extract_signals(question)
        sig.needs = sig.needs + [n for n in asked.needs if n not in sig.needs]
        sig.buying_signal = sig.buying_signal or asked.buying_signal
        sig.asks_price = sig.asks_price or asked.asks_price
        sig.wants_price = sig.wants_price or asked.wants_price

    def _fits_reply(self, sentence: str, spoken: List[str]) -> bool:
        """Keeps replies short: the first sentence always plays, then only what fits the word limit.
        A pitch or company-introduction turn has a larger limit."""
        if not spoken:
            return True
        words = len(" ".join(spoken).split()) + len(sentence.split())
        if self.stage.is_long_turn(bool(self.knowledge.company_intro)):
            if sentence.rstrip().endswith("?"):
                return len(spoken) < MAX_PITCH_SENTENCES and words <= MAX_PITCH_WORDS_WITH_QUESTION
            return len(spoken) < MAX_PITCH_SENTENCES - 1 and words <= MAX_PITCH_WORDS
        if sentence.rstrip().endswith("?"):
            return len(spoken) < MAX_SENTENCES_PER_REPLY and words <= MAX_REPLY_WORDS_WITH_QUESTION
        return len(spoken) < 2 and words <= MAX_REPLY_WORDS

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
            self._end("manager_callback")
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
                db.add(Complaint(client_id=self.identity.client_id, phone_number=self.identity.phone_number,
                                 lead_id=self.identity.lead_id,
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
        if intent == "opt_out":
            last_customer = next((t["text"] for t in reversed(self.transcript) if t["speaker"] == "customer"), "")
            clarity = opt_out_clarity(last_customer)
            if clarity == "none" or (clarity == "unclear" and "opt_out_confirm" in self._handled_intents):
                logger.info(f"Observer opt-out ignored on a short answer on {self.identity.call_id}")
                return
            if clarity == "unclear":
                intent = "opt_out_confirm"
        logger.warning(f"Observer late intent '{intent}' ({confidence:.2f}) on {self.identity.call_id}")
        self._emit("late_intent", {"intent": intent, "confidence": confidence})
        if self.late_intent_handler:
            await self.late_intent_handler(intent)

    async def handle_late_intent(self, intent: str) -> ScriptedTurn:
        if intent == "opt_out_confirm":
            self._handled_intents.add(intent)
            self.awaiting_opt_out_confirm = True
            return self._scripted([Utterance(OPT_OUT_CONFIRM_QUESTION)], intent)
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

    def _price_target(self, text: str) -> Optional[tuple]:
        """The (sku, plan) a price question means: the main product, unless another plan is named."""
        main = self.knowledge.primary_product()
        if main is None:
            return None
        t = text.lower()
        sku = next((c.sku for c in self.knowledge.catalog.values()
                    if c.sku != main.sku and c.name.lower().split()[-1] in t), main.sku)
        plan = "Annual" if re.search(r"\b(annual|yearly|year|saal|salana)\b", t) else "Monthly"
        if (sku, plan) in self.knowledge.catalog:
            return sku, plan
        return (sku, main.plan) if (sku, main.plan) in self.knowledge.catalog else None

    async def _price_line(self) -> Optional[Utterance]:
        """Answers a price question from the catalog, so the answer does not depend on the model."""
        if not self.price_requested:
            return None
        self.price_requested = False
        text = next((t["text"] for t in reversed(self.transcript) if t["speaker"] == "customer"), "")
        target = self._price_target(text)
        if target is None or target in self.tools._presented_offers:  # the offer price was already quoted
            return None
        args = {"sku": target[0], "plan": target[1]}
        self.tools.price_allowed = True
        result = await self.tools.execute("get_price", args)
        self._emit("tool_call", {"tool": "get_price", "arguments": args, "result": result})
        return Utterance(result["approved_phrasing"]) if result.get("ok") else None

    def _trim_name(self, sentence: str) -> str:
        """Removes the customer's name when it was used too recently; models tend to open every reply with it."""
        name = (self.identity.customer_name or "").split()
        if not name:
            return sentence
        pattern = re.compile(rf"(?P<lead>[,—–-]?\s*)\b{re.escape(name[0])}(?:\s+\w+)?\s+(?:ji|sir|sahab)\b(?!\s+(?:se|ko|ka|ki|ke|ne|par|hi)\b)(?P<trail>\s*[,—–-]?\s*)", re.I)
        if not pattern.search(sentence):
            return sentence
        if self._replies_since_name >= NAME_EVERY_REPLIES and not self._name_in_reply:
            self._name_in_reply = True
            return sentence

        def replace(m: re.Match) -> str:
            if m.start() == 0 or not re.search(r"\w", sentence[m.end():]):
                return ""
            # Without a comma the name may be part of the sentence, so it stays
            return ", " if re.search(r"[,—–-]", m.group("lead") + m.group("trail")) else m.group(0)

        trimmed = re.sub(r"\s+([.?!,])", r"\1", pattern.sub(replace, sentence)).strip()
        if not re.search(r"\w", trimmed):
            return sentence
        return trimmed[0].upper() + trimmed[1:]

    async def stream_reply(self) -> AsyncIterator[Utterance]:
        """Generates the reply sentence by sentence; every sentence passes the output guard first."""
        if self.llm is None:
            yield self._phrase(P.NETWORK_ISSUE_FALLBACK)
            self._end("failed", reason="no_llm")
            return

        stage_prompt = self.knowledge.stage_prompts.get(self.stage.current_stage.value, "")
        self.auth.authorize_text(stage_prompt)
        answer, kind = self._objection_context()
        started = time.monotonic()
        self._name_in_reply = False
        spoken: List[str] = []
        price_line = await self._price_line()
        self._said_amount = None
        if price_line:
            spoken.append(price_line.text)
            amount = re.search(r"₹[\d,]+", price_line.text)
            self._said_amount = amount.group(0) if amount else None
            yield price_line
        messages = build_messages(self.knowledge, self.stage, self.state, self.identity.customer_name,
                                  self.history, answer, kind, offered_slots=self.tools.offered_slot_phrases,
                                  already_said=price_line.text if price_line else None)

        ttft_ms: Optional[float] = None
        filler_played = False
        last_was_replacement = False

        try:
            for _round in range(MAX_TOOL_ROUNDS + 1):
                buffer = SentenceBuffer()
                tool_calls = []
                round_text = ""
                # The last round must answer in words. The tool list still has to be sent, because the
                # API rejects a conversation that has tool results but no tools.
                extra = {"tool_choice": "none"} if _round == MAX_TOOL_ROUNDS else None
                async for ev in self.llm.stream_chat(messages, tools=tool_schemas_for(self.knowledge),
                                                     extra_body=extra, purpose="reply"):
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
                            if out and self._fits_reply(out.text, spoken):
                                last_was_replacement = out.kind == "guard_replacement"
                                spoken.append(out.text)
                                yield out
                    elif ev.type == "tool_calls":
                        tool_calls = ev.tool_calls
                    elif ev.type == "done" and ev.usage:
                        self.usage.add(ev.usage)
                for sentence in buffer.flush():
                    out = self._guarded(sentence, last_was_replacement)
                    if out and self._fits_reply(out.text, spoken):
                        last_was_replacement = out.kind == "guard_replacement"
                        spoken.append(out.text)
                        yield out

                if not tool_calls or _round == MAX_TOOL_ROUNDS:
                    break
                messages.append({"role": "assistant", "content": round_text or None, "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments or "{}"},
                     **({"extra_content": tc.extra_content} if tc.extra_content else {})}
                    for tc in tool_calls]})
                for tc in tool_calls:
                    args = tc.parsed_arguments()
                    result = await self.tools.execute(tc.name, args)
                    self._emit("tool_call", {"tool": tc.name, "arguments": args, "result": result})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
        except LLMUnavailableError as e:
            logger.error(f"LLM unavailable mid-call {self.identity.call_id}: {e}")
            m.record_vendor_error("llm", "unavailable")
            if self.outcome in CONFIRMED_OUTCOMES:
                # The action already succeeded: confirm it instead of telling the customer something broke
                confirmation = Utterance(self.tools.last_confirmation)
                spoken.append(confirmation.text)
                yield confirmation
            else:
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
        self._replies_since_name = 0 if self._name_in_reply else self._replies_since_name + 1
        if self.outcome in ("payment_link_sent", "demo_booked") and self.stage.current_stage != SalesStage.COMPLETED:
            self.stage.transition_to(SalesStage.COMPLETED, f"Action confirmed: {self.outcome}")
        self.last_turn_metrics = {"llm_ttft_ms": ttft_ms, "reply_ms": (time.monotonic() - started) * 1000,
                                  "sentences": len(spoken)}
        self._emit("agent_reply", {"text": reply, **self.last_turn_metrics})

    def _guarded(self, sentence: str, last_was_replacement: bool) -> Optional[Utterance]:
        sentence = self._trim_name(sentence)
        if self._said_amount and self._said_amount in sentence and not sentence.rstrip().endswith("?"):
            return None  # code already spoke this price on this turn
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
