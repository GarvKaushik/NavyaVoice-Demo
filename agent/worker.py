"""LiveKit Agents worker for outbound Hinglish sales calls.

    python -m agent.worker console   # talk to the agent with your mic/speakers (no telephony)
    python -m agent.worker dev       # register with the LiveKit server (hot reload)
    python -m agent.worker start     # production

Per call: explicit dispatch with metadata (lead, campaign, phone) -> dial out over
the Plivo SIP trunk -> answering-machine detection -> pre-rendered disclosure ->
Sarvam streaming STT (Hinglish codemix, 8 kHz) -> DialogueEngine (fast path,
stage prompt, LLM + tools, output guard) -> Bulbul v4 Flash at 8 kHz with phrase
and sentence caches -> outcome, cost ledger and events written to Postgres.
"""

import asyncio
import json
import logging
import statistics
import time
import uuid
from typing import AsyncIterable, Optional

from dotenv import load_dotenv

load_dotenv()  # LiveKit and plugins read LIVEKIT_* / keys from the process environment

from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    ModelSettings,
    StopResponse,
    cli,
    llm as lk_llm,
    metrics as lk_metrics,
    stt as lk_stt,
    tts as lk_tts,
)
from livekit.plugins import openai as lk_openai, sarvam, silero

from agent.dialogue import DialogueEngine
from agent.knowledge import load_knowledge
from agent.tools import CallIdentity
from config.settings import get_settings
from guardrails.output_guard import SentenceBuffer
from llm.client import LLMClient
from observability import metrics as m
from observability.call_recorder import CallRecorder
from observability.logging_setup import setup_logging
from observability.tracing import setup_tracing
from speech.lexicon import KEYTERMS
from speech.normalizer import HinglishNormalizer
from speech.phrase_cache import PreRenderedPhraseCache as P, SentenceAudioCache

logger = logging.getLogger("agent.worker")
settings = get_settings()

FRAME_MS = 20
VAD_MIN_SILENCE = 0.2  # seconds of silence before the VAD reports end of speech


def pcm_frames(pcm: bytes, sample_rate: int):
    """Splits raw 16-bit mono PCM into 20 ms AudioFrames."""
    step = int(sample_rate * FRAME_MS / 1000) * 2
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        if len(chunk) < step:
            chunk = chunk + b"\x00" * (step - len(chunk))
        yield rtc.AudioFrame(data=chunk, sample_rate=sample_rate, num_channels=1, samples_per_channel=len(chunk) // 2)


def build_stt() -> lk_stt.STT:
    """Sarvam streaming STT with client flush.

    Local VAD decides end of turn and flushes the stream; Sarvam returns the final
    transcript ~90-160 ms later, instead of waiting ~930 ms for its own server-side
    endpointing. The plugin supports flush but does not advertise it, so the
    capability is declared here. (No same-vendor fallback adapter: it would not
    forward the flush, and a second Sarvam model does not cover a Sarvam outage.)
    """
    from dataclasses import replace

    stt = sarvam.STT(language=settings.SARVAM_STT_LANGUAGE_CODE, model=settings.SARVAM_STT_MODEL,
                     mode=settings.SARVAM_STT_MODE, api_key=settings.SARVAM_API_KEY,
                     sample_rate=settings.SARVAM_STT_SAMPLE_RATE, flush_signal=True,
                     prompt="Product names: " + ", ".join(KEYTERMS))
    stt._capabilities = replace(stt.capabilities, manual_flush=True)
    return stt


def build_tts() -> lk_tts.TTS:
    def make(model: str, speaker: str) -> sarvam.TTS:
        return sarvam.TTS(target_language_code="hi-IN", model=model, speaker=speaker,
                          speech_sample_rate=settings.BULBUL_TTS_SAMPLE_RATE, pace=settings.BULBUL_TTS_PACE,
                          api_key=settings.SARVAM_API_KEY, output_audio_codec="linear16", enable_preprocessing=False)
    primary = make(settings.BULBUL_TTS_MODEL, settings.BULBUL_TTS_VOICE_ID)
    if settings.BULBUL_TTS_FALLBACK_MODEL and settings.BULBUL_TTS_FALLBACK_MODEL != settings.BULBUL_TTS_MODEL:
        fallback = make(settings.BULBUL_TTS_FALLBACK_MODEL, settings.BULBUL_TTS_FALLBACK_VOICE_ID)
        return lk_tts.FallbackAdapter([primary, fallback], sample_rate=settings.BULBUL_TTS_SAMPLE_RATE)
    return primary


def placeholder_llm() -> lk_llm.LLM:
    """The session requires an LLM object; replies come from DialogueEngine via llm_node.
    It is also what answering-machine detection classifies greetings with."""
    return lk_openai.LLM(model=settings.REALTIME_LLM_MODEL, base_url=settings.REALTIME_LLM_BASE_URL,
                         api_key=settings.REALTIME_LLM_API_KEY, temperature=0.0)


class SalesAgent(Agent):
    """Bridges LiveKit's voice pipeline to the DialogueEngine."""

    def __init__(self, engine: DialogueEngine, phrases: P, sentence_cache: SentenceAudioCache, call: "CallSession"):
        super().__init__(instructions="Replies are produced by DialogueEngine in llm_node.")
        self.engine = engine
        self.phrases = phrases
        self.sentence_cache = sentence_cache
        self.call = call

    async def on_enter(self) -> None:
        for u in self.engine.opening():
            self.session.say(u.text, allow_interruptions=False)
        self.call.recorder.mark_disclosure()
        self.call.apply_endpointing()

    async def on_user_turn_completed(self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage) -> None:
        text = new_message.text_content or ""
        self.call.turn_started = time.monotonic()
        self.call.silence_prompts = 0
        scripted = await self.engine.pre_turn(text, agent_was_speaking=self.call.agent_was_speaking_at_user_start)
        if scripted is None:
            return  # llm_node generates the reply
        for u in scripted.utterances:
            self.session.say(u.text, allow_interruptions=False)
        if self.engine.pending_action:
            asyncio.create_task(self.call.execute_pending_action())
        raise StopResponse()

    async def llm_node(self, chat_ctx: lk_llm.ChatContext, tools, model_settings: ModelSettings) -> AsyncIterable[str]:
        async for u in self.engine.stream_reply():
            yield u.text + " "
        self.call.apply_endpointing()
        if self.engine.pending_action:
            asyncio.create_task(self.call.execute_pending_action())

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings) -> AsyncIterable[rtc.AudioFrame]:
        # Text arrives in arbitrary pieces (word by word for say()); reassemble whole sentences
        # so cached phrases match and Bulbul never receives punctuation-only fragments.
        buffer = SentenceBuffer()
        phrase_texts = [self.phrases.get_text(pid) for pid in self.phrases.CANONICAL_TEXTS]

        async def raw_sentences():
            async for chunk in text:
                for s in buffer.push(chunk):
                    yield s
            for s in buffer.flush():
                yield s

        async def units():
            """Joins consecutive sentences that together form a pre-rendered phrase."""
            pending = ""
            async for s in raw_sentences():
                candidate = f"{pending} {s}".strip() if pending else s
                if candidate in phrase_texts:
                    yield candidate
                    pending = ""
                elif any(p.startswith(candidate) for p in phrase_texts):
                    pending = candidate
                else:
                    if pending:
                        yield pending
                    pending = ""
                    if s in phrase_texts or not any(p.startswith(s) for p in phrase_texts):
                        yield s
                    else:
                        pending = s
            if pending:
                yield pending

        async for frame in self._speak_sentences(units()):
            yield frame

    async def _speak_sentences(self, sentences: AsyncIterable[str]) -> AsyncIterable[rtc.AudioFrame]:
        sample_rate = settings.BULBUL_TTS_SAMPLE_RATE
        async for sentence in sentences:
            sentence = sentence.strip()
            if not any(ch.isalnum() for ch in sentence):
                continue
            phrase_id = self.phrases.find_by_text(sentence)
            pcm = self.phrases.get_audio(phrase_id) if phrase_id else self.sentence_cache.get(sentence)
            if pcm:
                self.call.recorder.add_tts_chars(len(sentence), cached=True)
                for frame in pcm_frames(pcm, sample_rate):
                    self.call.on_first_audio()
                    yield frame
                continue
            spoken = HinglishNormalizer.normalize(sentence)
            self.call.recorder.add_tts_chars(len(spoken), cached=False)
            collected = bytearray()
            async for ev in self.session.tts.synthesize(spoken):
                self.call.on_first_audio()
                collected += bytes(ev.frame.data)
                yield ev.frame
            if self.call.cacheable(sentence):
                self.sentence_cache.put(sentence, bytes(collected))


class CallSession:
    """Per-call orchestration: dialing, timers, actions and accounting."""

    def __init__(self, ctx: JobContext, identity: CallIdentity, engine: DialogueEngine, console: bool):
        self.ctx = ctx
        self.identity = identity
        self.engine = engine
        self.console = console
        self.session: Optional[AgentSession] = None
        self.recorder = CallRecorder(identity, console=console)
        self.turn_started: Optional[float] = None
        self.latencies_ms: list = []
        self.last_eou_delay: float = 0.0
        self.agent_was_speaking_at_user_start = False
        self.silence_prompts = 0
        self.agent_speaking_since: Optional[float] = None
        self.agent_talk_seconds = 0.0
        self.started = time.monotonic()
        self.false_interruptions = 0
        self._ending = False

    def cacheable(self, sentence: str) -> bool:
        """Only generic sentences are cached on disk: no digits, no customer name."""
        name = (self.identity.customer_name or "").split()[0].lower() if self.identity.customer_name else ""
        return not any(ch.isdigit() for ch in sentence) and (not name or name not in sentence.lower())

    def on_first_audio(self) -> None:
        if self.turn_started is not None:
            latency = (time.monotonic() - self.turn_started) * 1000 + self.last_eou_delay * 1000
            self.latencies_ms.append(latency)
            m.observe_voice_to_voice(latency / 1000)
            self.recorder.add_event("latency", {"voice_to_voice_ms": round(latency),
                                                "llm_ttft_ms": self.engine.last_turn_metrics.get("llm_ttft_ms"),
                                                "eou_delay_ms": round(self.last_eou_delay * 1000)})
            logger.info(f"turn latency ~{latency:.0f} ms (eou {self.last_eou_delay * 1000:.0f} ms)")
            self.turn_started = None

    def apply_endpointing(self) -> None:
        if self.session is None:
            return
        # Stage-aware pause (300 ms after yes/no questions, 600 ms after open ones), minus the
        # VAD's own 200 ms silence window that runs before endpointing starts counting
        delay = max(self.engine.stage.endpointing_delay_ms / 1000 - VAD_MIN_SILENCE, 0.1)
        try:
            self.session.update_options(endpointing_opts={"mode": "fixed", "min_delay": delay,
                                                          "max_delay": max(delay + 1.5, 2.0)})
        except Exception as e:
            logger.debug(f"endpointing update skipped: {e}")

    def wire_events(self, session: AgentSession) -> None:
        @session.on("metrics_collected")
        def _on_metrics(ev):
            mt = ev.metrics
            if isinstance(mt, lk_metrics.STTMetrics):
                self.recorder.add_stt_seconds(mt.audio_duration)
            elif isinstance(mt, lk_metrics.EOUMetrics):
                self.last_eou_delay = mt.end_of_utterance_delay
            elif isinstance(mt, lk_metrics.TTSMetrics) and mt.ttfb > settings.TTS_FIRST_BYTE_TIMEOUT_SECONDS:
                m.record_vendor_error("tts", "slow_first_byte")

        @session.on("user_state_changed")
        def _on_user_state(ev):
            if ev.new_state == "speaking":
                self.agent_was_speaking_at_user_start = session.agent_state == "speaking"
            elif ev.new_state == "away" and not self._ending:
                asyncio.create_task(self.on_silence())

        @session.on("agent_state_changed")
        def _on_agent_state(ev):
            now = time.monotonic()
            if ev.new_state == "speaking":
                self.agent_speaking_since = now
            elif self.agent_speaking_since is not None:
                self.agent_talk_seconds += now - self.agent_speaking_since
                self.agent_speaking_since = None

        @session.on("agent_false_interruption")
        def _on_false_interruption(ev):
            self.false_interruptions += 1

        @session.on("user_input_transcribed")
        def _on_transcribed(ev):
            if ev.is_final:
                logger.info(f"STT final: {ev.transcript!r}")

        @session.on("error")
        def _on_error(ev):
            recoverable = getattr(ev.error, "recoverable", True)
            logger.error(f"session error (recoverable={recoverable}): {ev.error}")
            if not recoverable and not self._ending:
                asyncio.create_task(self.fail_gracefully(str(ev.error)))

        @session.on("close")
        def _on_close(ev):
            asyncio.create_task(self.finalize())

    async def on_silence(self) -> None:
        """6 s of silence: re-prompt once, then close politely and schedule a callback."""
        self.silence_prompts += 1
        if self.silence_prompts == 1:
            self.session.say(self.engine.phrases.get_text(P.SILENCE_REPROMPT))
            return
        self._ending = True
        await self.engine.tools.tool_schedule_callback("2 ghante baad")
        handle = self.session.say(self.engine.phrases.get_text(P.SILENCE_CLOSE), allow_interruptions=False)
        await handle.wait_for_playout()
        self.engine.tools.outcome = "no_response"
        await self.hangup()

    async def fail_gracefully(self, reason: str) -> None:
        self._ending = True
        try:
            handle = self.session.say(self.engine.phrases.get_text(P.NETWORK_ISSUE_FALLBACK), allow_interruptions=False)
            await asyncio.wait_for(handle.wait_for_playout(), timeout=8)
        except Exception:
            pass
        await self.engine.tools.tool_schedule_callback("2 ghante baad")
        self.engine.tools.outcome = "dropped"
        self.recorder.error = reason
        await self.hangup()

    async def execute_pending_action(self) -> None:
        action = self.engine.pending_action
        if action is None or self._ending:
            return
        self._ending = True
        await asyncio.sleep(0.2)
        try:
            await asyncio.wait_for(self.session.wait_for_idle(), timeout=30)  # let the last line finish playing
        except Exception as e:
            logger.debug(f"wait_for_idle: {e!r}")
        if action.kind == "transfer" and not self.console:
            try:
                await self.ctx.transfer_sip_participant("caller", f"tel:{action.detail['to']}")
                logger.info(f"Transferred {self.identity.call_id} to {action.detail['to']}")
            except Exception as e:
                logger.error(f"Transfer failed: {e!r}; scheduling callback")
                await self.engine.tools.tool_schedule_callback("kal subah")
                self.engine.tools.outcome = "callback_scheduled"
        await self.hangup()

    async def late_intent(self, intent: str) -> None:
        """Observer caught a critical intent the regex missed: interrupt and handle it."""
        if self._ending:
            return
        self.session.interrupt()
        scripted = await self.engine.handle_late_intent(intent)
        for u in scripted.utterances:
            self.session.say(u.text, allow_interruptions=False)
        if self.engine.pending_action:
            await self.execute_pending_action()

    async def hangup(self) -> None:
        self._ending = True
        if self.console:
            logger.info(f"Call ended: outcome={self.engine.outcome}")
            await self.finalize()
            return
        try:
            await self.ctx.api.room.delete_room(api.DeleteRoomRequest(room=self.ctx.room.name))
        except Exception as e:
            logger.warning(f"delete_room failed: {e!r}")
        self.ctx.shutdown(reason=f"outcome:{self.engine.outcome}")

    async def finalize(self) -> None:
        if self.recorder.finalized:
            return
        await self.engine.drain_observer(timeout=2)
        duration = time.monotonic() - self.started
        lat = sorted(self.latencies_ms)
        self.recorder.p50 = statistics.median(lat) if lat else None
        self.recorder.p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None
        self.recorder.false_interruptions = self.false_interruptions
        self.recorder.talk_share = round(self.agent_talk_seconds / duration, 3) if duration > 0 else None
        await self.recorder.finalize(self.engine, duration)
        m.GAUGE_ACTIVE_CALLS and m.GAUGE_ACTIVE_CALLS.dec()


# ------------------------------------------------------------------------------
server = AgentServer(
    drain_timeout=settings.WORKER_DRAIN_TIMEOUT_SECONDS,
    load_threshold=0.95,
    num_idle_processes=2,
)


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=VAD_MIN_SILENCE,
                                           prefix_padding_duration=settings.SARVAM_VAD_PRE_ROLL_MS / 1000)
    phrases = P()
    phrases.load()
    proc.userdata["phrases"] = phrases
    proc.userdata["sentence_cache"] = SentenceAudioCache()


server.setup_fnc = prewarm


def _identity_from(ctx: JobContext) -> tuple:
    meta = {}
    raw = ctx.job.metadata or ""
    if raw:
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Job metadata is not JSON: {raw[:100]}")
    console = ctx.is_fake_job()
    identity = CallIdentity(
        call_id=meta.get("call_id") or ctx.room.name or f"console_{uuid.uuid4().hex[:8]}",
        lead_id=meta.get("lead_id", "LEAD-TEST-001"),
        campaign_id=meta.get("campaign_id", "CAMP-REENGAGE-Q4"),
        phone_number=meta.get("phone_number", "+919876543210"),
        customer_name=meta.get("customer_name", "Rajesh"),
        segment=meta.get("segment"),
        timezone=meta.get("timezone", settings.TIMEZONE),
    )
    return identity, meta, console


@server.rtc_session(agent_name=settings.LIVEKIT_AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    setup_logging("agent-worker")
    setup_tracing("agent-worker")
    identity, meta, console = _identity_from(ctx)
    ctx.log_context_fields = {"call_id": identity.call_id, "lead_id": identity.lead_id}

    knowledge = await load_knowledge(identity.campaign_id)
    llm_client = LLMClient.realtime()
    engine = DialogueEngine(identity, knowledge, llm_client, observer_llm=llm_client)
    call = CallSession(ctx, identity, engine, console)
    engine._emit = call.recorder.add_event
    engine.late_intent_handler = call.late_intent
    ctx.add_shutdown_callback(call.finalize)
    ctx.add_shutdown_callback(llm_client.aclose)

    session = AgentSession(
        stt=build_stt(),
        tts=build_tts(),
        llm=placeholder_llm(),
        vad=ctx.proc.userdata["vad"],
        turn_handling={
            # Local VAD ends the turn and flushes Sarvam (benchmarked ~430 ms vs ~930 ms with "stt" endpointing)
            "turn_detection": "vad",
            "endpointing": {"mode": "fixed", "min_delay": 0.1, "max_delay": 2.5},
            "interruption": {
                "enabled": True,
                "mode": "vad",  # local; "adaptive" needs LiveKit Cloud inference
                "min_duration": 0.6,
                "min_words": 2,  # backchannels ("haan", "hmm", "ji") never interrupt
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.5,
            },
        },
        user_away_timeout=6.0,
    )
    call.session = session
    call.wire_events(session)
    agent = SalesAgent(engine, ctx.proc.userdata["phrases"], ctx.proc.userdata["sentence_cache"], call)

    await ctx.connect()
    if m.GAUGE_ACTIVE_CALLS:
        m.GAUGE_ACTIVE_CALLS.inc()
    await call.recorder.start(knowledge.script_version, meta.get("caller_id") or settings.PLIVO_CALLER_ID_PROMOTIONAL)

    if console:
        await session.start(agent=agent, room=ctx.room)
        return

    # Outbound: dial the customer over the Plivo SIP trunk, with answering-machine detection
    if not settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
        logger.error("LIVEKIT_SIP_OUTBOUND_TRUNK_ID not set; cannot dial")
        await call.recorder.finalize_unanswered("failed", "no outbound trunk configured")
        ctx.shutdown()
        return

    await session.start(agent=agent, room=ctx.room)
    outcome = await dial_customer(ctx, session, identity, meta)
    if outcome != "answered":
        await call.recorder.finalize_unanswered(outcome)
        await call.hangup()


async def dial_customer(ctx: JobContext, session: AgentSession, identity: CallIdentity, meta: dict) -> str:
    """Creates the SIP participant; returns 'answered' or an unanswered outcome."""
    from livekit.agents import AMD

    request = api.CreateSIPParticipantRequest(
        sip_trunk_id=settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID,
        sip_call_to=identity.phone_number,
        sip_number=meta.get("caller_id") or settings.PLIVO_CALLER_ID_PROMOTIONAL,
        room_name=ctx.room.name,
        participant_identity="caller",
        participant_name=identity.customer_name,
        wait_until_answered=True,
    )
    try:
        async with AMD(session, llm=None, stt=None, ivr_detection=False, interrupt_on_machine=True,
                       participant_identity="caller", suppress_compatibility_warning=True) as detector:
            await ctx.api.sip.create_sip_participant(request)
            result = await detector.execute()
        category = str(getattr(result, "category", "human")).lower()
        if "machine" in category:
            logger.info(f"AMD: machine answered ({category}); hanging up")
            return "voicemail"
        return "answered"
    except api.TwirpError as e:
        status = str((e.metadata or {}).get("sip_status_code", ""))
        outcome = {"486": "busy", "600": "busy", "480": "no_answer", "408": "no_answer", "487": "no_answer",
                   "404": "invalid_number", "484": "invalid_number"}.get(status, "failed")
        logger.info(f"Dial failed: sip={status} {e.message} -> {outcome}")
        return outcome


if __name__ == "__main__":
    cli.run_app(server)
