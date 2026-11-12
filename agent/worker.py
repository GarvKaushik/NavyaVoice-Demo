"""LiveKit Agents worker for outbound Hinglish sales calls.

    python -m agent.worker console   # talk to the agent with your mic/speakers (no telephony)
    python -m agent.worker dev       # register with the LiveKit server (hot reload)
    python -m agent.worker start     # production

Per call: explicit dispatch with metadata (lead, campaign, phone) -> dial out over
the Plivo SIP trunk -> answering-machine detection -> pre-rendered disclosure ->
STT (STT service, or Sarvam streaming) -> DialogueEngine (fast path, stage prompt,
LLM + tools, output guard) -> TTS (TTS service, Piper, OpenAI, or Bulbul v4 Flash) with
phrase and sentence caches -> outcome, cost ledger and events written to Postgres.
"""

import asyncio
import json
import logging
import statistics
import threading
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
from livekit.plugins import openai as lk_openai, silero

from agent.dialogue import DialogueEngine
from agent.knowledge import KnowledgeUnavailable, load_knowledge
from agent.tools import CONFIRMED_OUTCOMES, CallIdentity
from config.settings import get_settings
from database.models import DEFAULT_CLIENT_ID
from guardrails.output_guard import SentenceBuffer
from llm.client import LLMClient, realtime_endpoints
from observability import metrics as m
from observability.call_recorder import CallRecorder
from observability.logging_setup import setup_logging
from observability.recording import start_recording
from observability.tracing import setup_tracing
from speech.lexicon import KEYTERMS
from speech.normalizer import HinglishNormalizer
from speech.phrase_cache import PreRenderedPhraseCache as P, SentenceAudioCache

logger = logging.getLogger("agent.worker")
settings = get_settings()

# Plugins register at import, on the main thread. Without Sarvam selected, its plugin is never loaded.
if (settings.STT_PROVIDER == "sarvam" or settings.TTS_PROVIDER == "sarvam"
        or (settings.TTS_PROVIDER == "server" and settings.TTS_FALLBACK_TO_SARVAM)):
    from livekit.plugins import sarvam

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


def build_stt(extra_terms: Optional[list] = None) -> lk_stt.STT:
    """Sarvam streaming STT with client flush.

    Local VAD decides end of turn and flushes the stream; Sarvam returns the final
    transcript ~90-160 ms later, instead of waiting ~930 ms for its own server-side
    endpointing. The plugin supports flush but does not advertise it, so the
    capability is declared here. (No same-vendor fallback adapter: it would not
    forward the flush, and a second Sarvam model does not cover a Sarvam outage.)
    """
    from dataclasses import replace

    if settings.STT_PROVIDER != "sarvam":
        from speech.local_stt import LocalSTT

        return LocalSTT(settings.STT_PROVIDER)
    stt = sarvam.STT(language=settings.SARVAM_STT_LANGUAGE_CODE, model=settings.SARVAM_STT_MODEL,
                     mode=settings.SARVAM_STT_MODE, api_key=settings.SARVAM_API_KEY,
                     sample_rate=settings.SARVAM_STT_SAMPLE_RATE, flush_signal=True,
                     prompt="Product names: " + ", ".join(extra_terms or KEYTERMS))
    stt._capabilities = replace(stt.capabilities, manual_flush=True)
    return stt


def build_tts(voice_id: Optional[str] = None) -> lk_tts.TTS:
    def make(model: str, speaker: str) -> "sarvam.TTS":
        return sarvam.TTS(target_language_code="hi-IN", model=model, speaker=speaker,
                          speech_sample_rate=settings.BULBUL_TTS_SAMPLE_RATE, pace=settings.BULBUL_TTS_PACE,
                          api_key=settings.SARVAM_API_KEY, output_audio_codec="linear16", enable_preprocessing=False)

    if settings.TTS_PROVIDER == "server":
        from speech.server_tts import ServerTTS

        service = ServerTTS()
        if settings.TTS_FALLBACK_TO_SARVAM and settings.sarvam_configured:
            return lk_tts.FallbackAdapter([service, make(settings.BULBUL_TTS_MODEL, settings.BULBUL_TTS_VOICE_ID)],
                                          sample_rate=settings.TTS_SERVER_SAMPLE_RATE)
        return service
    if settings.TTS_PROVIDER == "piper":
        from speech.piper_tts import PiperLiveTTS

        return PiperLiveTTS()
    if settings.TTS_PROVIDER == "openai":
        extra = {"instructions": settings.OPENAI_TTS_INSTRUCTIONS} if settings.OPENAI_TTS_MODEL == "gpt-4o-mini-tts" else {}
        return lk_openai.TTS(model=settings.OPENAI_TTS_MODEL, voice=voice_id or settings.OPENAI_TTS_VOICE, speed=settings.OPENAI_TTS_SPEED,
                             api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL,
                             sample_rate=settings.OPENAI_TTS_SAMPLE_RATE, response_format="pcm", **extra)

    primary = make(settings.BULBUL_TTS_MODEL, voice_id or settings.BULBUL_TTS_VOICE_ID)
    if settings.BULBUL_TTS_FALLBACK_MODEL and settings.BULBUL_TTS_FALLBACK_MODEL != settings.BULBUL_TTS_MODEL:
        fallback = make(settings.BULBUL_TTS_FALLBACK_MODEL, settings.BULBUL_TTS_FALLBACK_VOICE_ID)
        return lk_tts.FallbackAdapter([primary, fallback], sample_rate=settings.BULBUL_TTS_SAMPLE_RATE)
    return primary


def placeholder_llm() -> lk_llm.LLM:
    """The session requires an LLM object; replies come from DialogueEngine via llm_node.
    It is also what answering-machine detection classifies greetings with."""
    endpoint = realtime_endpoints()[0]
    return lk_openai.LLM(model=endpoint.model, base_url=endpoint.base_url, api_key=endpoint.api_key, temperature=0.0)


class SalesAgent(Agent):
    """Bridges LiveKit's voice pipeline to the DialogueEngine."""

    def __init__(self, engine: DialogueEngine, phrases: P, sentence_cache: SentenceAudioCache, call: "CallSession"):
        super().__init__(instructions="Replies are produced by DialogueEngine in llm_node.")
        self.engine = engine
        self.phrases = phrases
        self.sentence_cache = sentence_cache
        self.call = call

    async def on_enter(self) -> None:
        # Browser and console calls: the person is already listening. Phone calls start before dialing, so the
        # greeting (with the AI disclosure) waits until the customer answers; see entrypoint.
        if self.call.console:
            self.speak_opening()

    def speak_opening(self) -> None:
        for u in self.engine.opening():
            self.session.say(u.text, allow_interruptions=False)
        self.call.recorder.mark_disclosure()
        self.call.apply_endpointing()

    async def on_user_turn_completed(self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage) -> None:
        text = new_message.text_content or ""
        self.call.turn_started = time.monotonic()
        self.call.tts_started = None
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
        sample_rate = settings.tts_sample_rate
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
            self.call.tts_started = self.call.tts_started or time.monotonic()
            collected = bytearray()
            async for ev in self.session.tts.synthesize(spoken):
                self.call.on_first_audio()
                collected += bytes(ev.frame.data)
                yield ev.frame
            if getattr(self.session.tts, "last_was_fallback", False):
                continue  # spoken by the TTS service's fallback voice; the cache holds the main voice only
            if phrase_id:
                self.phrases.save(phrase_id, bytes(collected))  # a client's own lines are rendered once, then reused
            elif self.call.cacheable(sentence):
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
        self.tts_started: Optional[float] = None
        self.latencies_ms: list = []
        self.last_eou_delay: float = 0.0
        self.agent_was_speaking_at_user_start = False
        self.silence_prompts = 0
        self.agent_speaking_since: Optional[float] = None
        self.agent_talk_seconds = 0.0
        self.started: Optional[float] = time.monotonic() if console else None  # set when the customer answers
        self.false_interruptions = 0
        self.time_limit: Optional[asyncio.Task] = None
        self._ending = False

    def mark_answered(self) -> None:
        """Duration and billing count from the moment the customer picks up."""
        self.started = time.monotonic()
        asyncio.create_task(self.recorder.mark_answered())
        asyncio.create_task(self._start_recording())

    async def _start_recording(self) -> None:
        key = await start_recording(self.ctx.api, self.ctx.room.name, self.identity.client_id, self.identity.call_id)
        if key:
            await self.recorder.set_recording(key)

    def cacheable(self, sentence: str) -> bool:
        """Only generic sentences are cached on disk: no digits, no customer name."""
        name = (self.identity.customer_name or "").split()[0].lower() if self.identity.customer_name else ""
        return not any(ch.isdigit() for ch in sentence) and (not name or name not in sentence.lower())

    def on_first_audio(self) -> None:
        if self.turn_started is not None:
            latency = (time.monotonic() - self.turn_started) * 1000 + self.last_eou_delay * 1000
            self.latencies_ms.append(latency)
            stt_ms = getattr(self.session.stt, "last_decode_ms", None) if self.session else None
            tts_ms = (time.monotonic() - self.tts_started) * 1000 if self.tts_started else None
            self.tts_started = None
            m.observe_voice_to_voice(latency / 1000)
            self.recorder.add_event("latency", {"voice_to_voice_ms": round(latency),
                                                "llm_ttft_ms": self.engine.last_turn_metrics.get("llm_ttft_ms"),
                                                "eou_delay_ms": round(self.last_eou_delay * 1000),
                                                "stt_ms": stt_ms and round(stt_ms), "tts_first_audio_ms": tts_ms and round(tts_ms)})
            ttft = self.engine.last_turn_metrics.get("llm_ttft_ms")
            logger.info(f"turn latency ~{latency:.0f} ms (eou {self.last_eou_delay * 1000:.0f} ms, "
                        f"stt {f'{stt_ms:.0f} ms' if stt_ms else 'streaming'}, "
                        f"llm first token {f'{ttft:.0f} ms' if ttft else 'scripted'}, "
                        f"tts first audio {f'{tts_ms:.0f} ms' if tts_ms else 'cached'})")
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
            logger.debug(f"user state: {ev.old_state} -> {ev.new_state}")
            if ev.new_state == "speaking":
                self.agent_was_speaking_at_user_start = session.agent_state == "speaking"
            elif ev.new_state == "away" and not self._ending:
                asyncio.create_task(self.on_silence())

        @session.on("agent_state_changed")
        def _on_agent_state(ev):
            logger.debug(f"agent state: {ev.old_state} -> {ev.new_state}")
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
        confirmed = self.engine.outcome in CONFIRMED_OUTCOMES  # a booked demo stays booked, with no extra callback
        if not confirmed:
            await self.engine.tools.tool_schedule_callback("2 ghante baad")
        closing = P.GOODBYE if confirmed else P.SILENCE_CLOSE
        handle = self.session.say(self.engine.phrases.get_text(closing), allow_interruptions=False)
        await handle.wait_for_playout()
        if not confirmed:
            self.engine.tools.outcome = "no_response"
        await self.hangup()

    async def end_after(self, seconds: float) -> None:
        """A time-limited call (the website demo) says goodbye and hangs up when its time is up."""
        await asyncio.sleep(seconds)
        if self._ending:
            return
        self._ending = True
        try:
            handle = self.session.say(self.engine.phrases.get_text(P.GOODBYE), allow_interruptions=False)
            await asyncio.wait_for(handle.wait_for_playout(), timeout=8)
        except Exception as e:
            logger.debug(f"goodbye before the time limit: {e!r}")
        await self.hangup()

    async def fail_gracefully(self, reason: str) -> None:
        self._ending = True
        confirmed = self.engine.outcome in CONFIRMED_OUTCOMES
        try:
            closing = P.GOODBYE if confirmed else P.NETWORK_ISSUE_FALLBACK
            handle = self.session.say(self.engine.phrases.get_text(closing), allow_interruptions=False)
            await asyncio.wait_for(handle.wait_for_playout(), timeout=8)
        except Exception:
            pass
        if not confirmed:
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
        if self.ctx.is_fake_job():
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
        duration = time.monotonic() - self.started if self.started is not None else 0.0
        lat = sorted(self.latencies_ms)
        self.recorder.p50 = statistics.median(lat) if lat else None
        self.recorder.p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None
        self.recorder.false_interruptions = self.false_interruptions
        self.recorder.talk_share = round(self.agent_talk_seconds / duration, 3) if duration > 0 else None
        await self.recorder.finalize(self.engine, duration)
        m.GAUGE_ACTIVE_CALLS and m.GAUGE_ACTIVE_CALLS.dec()


# ------------------------------------------------------------------------------
def _server_options() -> dict:
    options = {"drain_timeout": settings.WORKER_DRAIN_TIMEOUT_SECONDS, "load_threshold": 0.95, "num_idle_processes": 2}
    if settings.WORKER_JOB_EXECUTOR:
        from livekit.agents import JobExecutorType

        options["job_executor_type"] = JobExecutorType(settings.WORKER_JOB_EXECUTOR)
    return options


server = AgentServer(**_server_options())


# Sample-rate pairs the calls resample between: phone 8 kHz, STT 8/16 kHz, TTS 8/24 kHz, WebRTC 48 kHz
_RESAMPLE_PAIRS = [(48000, 8000), (48000, 16000), (8000, 48000), (16000, 48000), (24000, 48000), (24000, 8000),
                   (22050, 48000)]


def prime_audio_resamplers() -> None:
    """Sets up the native resampler's shared tables once, on the main thread, before any call starts. With calls
    running as threads (Windows), two streams creating their first resampler at the same moment can otherwise race
    on those tables and abort inside livekit_ffi."""
    for source, target in _RESAMPLE_PAIRS:
        try:
            resampler = rtc.AudioResampler(source, target, num_channels=1)
            samples = source // 50  # 20 ms
            resampler.push(rtc.AudioFrame(data=b"\0\0" * samples, sample_rate=source, num_channels=1,
                                          samples_per_channel=samples))
            resampler.flush()
        except Exception as e:
            logger.debug(f"resampler warm-up {source}->{target} skipped: {e!r}")


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=VAD_MIN_SILENCE,
                                           prefix_padding_duration=settings.SARVAM_VAD_PRE_ROLL_MS / 1000)
    proc.userdata["phrases"] = {}
    proc.userdata["sentence_cache"] = {settings.tts_voice: SentenceAudioCache()}
    if settings.TTS_PROVIDER == "piper":
        from speech.piper_tts import load_voice

        # In the background: it can take longer than the time a worker process is allowed to start up
        threading.Thread(target=load_voice, daemon=True).start()


server.setup_fnc = prewarm


def _identity_from(ctx: JobContext) -> tuple:
    meta = {}
    raw = ctx.job.metadata or ""
    if raw:
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Job metadata is not JSON: {raw[:100]}")
    console = ctx.is_fake_job() or bool(meta.get("test_call"))  # a browser test call has no phone leg and is not billed
    identity = CallIdentity(
        call_id=meta.get("call_id") or ctx.room.name or f"console_{uuid.uuid4().hex[:8]}",
        lead_id=meta.get("lead_id", "LEAD-TEST-001"),
        campaign_id=meta.get("campaign_id", "CAMP-REENGAGE-Q4"),
        phone_number=meta.get("phone_number", "+919876543210"),
        customer_name=meta.get("customer_name", "Rajesh"),
        segment=meta.get("segment"),
        timezone=meta.get("timezone", settings.TIMEZONE),
        client_id=meta.get("client_id", DEFAULT_CLIENT_ID),
    )
    return identity, meta, console


@server.rtc_session(agent_name=settings.LIVEKIT_AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    setup_logging("agent-worker")
    setup_tracing("agent-worker")
    identity, meta, console = _identity_from(ctx)
    ctx.log_context_fields = {"call_id": identity.call_id, "lead_id": identity.lead_id}

    try:
        knowledge = await load_knowledge(identity.campaign_id)
    except KnowledgeUnavailable as e:
        logger.error(f"Not dialing {identity.call_id}: {e}")
        await CallRecorder(identity, console=console).finalize_unanswered("failed", str(e))
        ctx.shutdown(reason="no_approved_content")
        return
    llm_client = LLMClient.realtime()
    engine = DialogueEngine(identity, knowledge, llm_client, observer_llm=llm_client)
    # Each client's phrases and each voice's sentence cache load once per worker process
    phrase_key = (knowledge.voice_id, knowledge.company_name, knowledge.persona_name, knowledge.complaint_contact)
    phrases = ctx.proc.userdata["phrases"].get(phrase_key)
    if phrases is None:
        engine.phrases.load()
        phrases = ctx.proc.userdata["phrases"][phrase_key] = engine.phrases
    engine.phrases = phrases
    sentence_cache = ctx.proc.userdata["sentence_cache"].setdefault(knowledge.voice_id,
                                                                    SentenceAudioCache(voice=knowledge.voice_id))
    call = CallSession(ctx, identity, engine, console)
    engine._emit = call.recorder.add_event
    engine.late_intent_handler = call.late_intent
    ctx.add_shutdown_callback(call.finalize)
    ctx.add_shutdown_callback(llm_client.aclose)

    tts = build_tts(knowledge.voice_id)
    if settings.TTS_PROVIDER == "server":
        ctx.add_shutdown_callback(tts.aclose)  # closes the call's connection to the TTS service
    session = AgentSession(
        stt=build_stt([knowledge.company_name] + sorted({c.name for c in knowledge.catalog.values()})),
        tts=tts,
        llm=placeholder_llm(),
        vad=ctx.proc.userdata["vad"],
        turn_handling={
            # Local VAD ends the turn and flushes the STT (on Sarvam, benchmarked ~430 ms vs ~930 ms with "stt" endpointing)
            "turn_detection": "vad",
            # The reply is built from the engine's history, which is updated in on_user_turn_completed.
            # Generating earlier would answer the previous turn.
            "preemptive_generation": {"enabled": False},
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
        # A slow local decode must not look like a silent customer
        user_away_timeout=6.0 if settings.STT_PROVIDER == "sarvam" else 6.0 + settings.STT_SERVER_TIMEOUT_SECONDS,
    )
    call.session = session
    call.wire_events(session)
    agent = SalesAgent(engine, phrases, sentence_cache, call)

    await ctx.connect()
    if m.GAUGE_ACTIVE_CALLS:
        m.GAUGE_ACTIVE_CALLS.inc()
    await call.recorder.start(knowledge.script_version, meta.get("caller_id") or settings.PLIVO_CALLER_ID_PROMOTIONAL,
                              knowledge.voice_id)

    if console:
        await session.start(agent=agent, room=ctx.room)
        if meta.get("max_seconds"):
            call.time_limit = asyncio.create_task(call.end_after(float(meta["max_seconds"])))
        return

    # Outbound: dial the customer over the Plivo SIP trunk, with answering-machine detection
    if not settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
        logger.error("LIVEKIT_SIP_OUTBOUND_TRUNK_ID not set; cannot dial")
        await call.recorder.finalize_unanswered("failed", "no outbound trunk configured")
        ctx.shutdown()
        return

    await session.start(agent=agent, room=ctx.room)
    outcome = await dial_customer(ctx, session, identity, meta, call)
    if outcome != "answered":
        await call.recorder.finalize_unanswered(outcome)
        await call.hangup()
        return
    agent.speak_opening()  # only now is someone on the line to hear the disclosure


async def dial_customer(ctx: JobContext, session: AgentSession, identity: CallIdentity, meta: dict,
                        call: CallSession) -> str:
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
            await ctx.api.sip.create_sip_participant(request)  # returns when the customer answers
            call.mark_answered()
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
    prime_audio_resamplers()
    cli.run_app(server)
