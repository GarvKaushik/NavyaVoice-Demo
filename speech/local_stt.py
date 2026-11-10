"""LiveKit STT adapter for the STT server, with the same streaming interface as the Sarvam plugin.

With STT_SERVER_STREAMING on, each call keeps one connection open and audio is sent as it arrives; the session's
VAD flush asks for the final transcript. Off, each utterance is sent in one go when it ends. Either way the
adapter keeps the utterance's audio, so a lost connection is retried once with the whole utterance, and a server
error, timeout or backlog can be answered by Sarvam, only if STT_FALLBACK_TO_SARVAM is on.
"""

import asyncio
import io
import logging
import time
import uuid
import wave
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import aiohttp
from livekit import rtc
from livekit.agents import stt as lk_stt
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr
from livekit.agents.utils import AudioBuffer, merge_frames

from config.settings import get_settings
from speech.service_auth import auth_headers
from speech.stt_server.audio import float_to_pcm16, pcm16_to_float, trim_silence

logger = logging.getLogger(__name__)

MAX_UTTERANCE_SECONDS = 30
PARTIAL_INTERVAL_SECONDS = 0.4
RECONNECT_SECONDS = 10  # after a failed connect, utterances are sent whole for this long before streaming is tried again
CONNECT_TIMEOUT_SECONDS = 5
LANGUAGE = "hi-IN"


class ServerDecodeError(RuntimeError):
    """The local server could not answer this utterance."""


class ServerConnectionError(ServerDecodeError):
    """The connection failed (rather than the server answering busy, an error, or nothing in time), so a retry can help."""


@dataclass
class Decoded:
    text: str
    source: str  # the engine name, or "sarvam" after a fallback
    decode_ms: float


Decoder = Callable[[bytes, int], Awaitable[str]]


async def decode_on_server(engine: str, pcm: bytes, sample_rate: int) -> str:
    """Sends one utterance to the server on its own connection. Raises ServerDecodeError on any failure."""
    s = get_settings()
    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(s.STT_SERVER_URL, max_msg_size=0, headers=auth_headers()) as ws:
                await ws.send_json({"type": "config", "engine": engine, "sample_rate": sample_rate})
                await ws.send_bytes(pcm)
                await ws.send_json({"type": "flush"})
                reply = await asyncio.wait_for(ws.receive_json(), timeout=s.STT_SERVER_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        raise ServerDecodeError(f"no answer within {s.STT_SERVER_TIMEOUT_SECONDS}s") from e
    except (aiohttp.ClientError, OSError, TypeError, ValueError) as e:
        raise ServerDecodeError(repr(e)) from e
    if reply.get("type") == "final":
        return reply.get("text") or ""
    raise ServerDecodeError(reply.get("message") or reply.get("type") or "unexpected reply")


class STTConnection:
    """One call's connection to the STT server. Audio goes out as it arrives; replies come back in request order."""

    def __init__(self, engine: str, sample_rate: int):
        self.engine = engine
        self.sample_rate = sample_rate
        self._http: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._lock = asyncio.Lock()

    @property
    def open(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """Raises ServerDecodeError when the server can't be reached."""
        await self.close()
        try:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=CONNECT_TIMEOUT_SECONDS))
            self._ws = await self._http.ws_connect(get_settings().STT_SERVER_URL, max_msg_size=0, headers=auth_headers())
            await self._ws.send_json({"type": "config", "engine": self.engine, "sample_rate": self.sample_rate})
        except (aiohttp.ClientError, OSError, ValueError) as e:
            await self.close()
            raise ServerConnectionError(repr(e)) from e

    async def send_audio(self, pcm: bytes) -> None:
        try:
            await self._ws.send_bytes(pcm)
        except (aiohttp.ClientError, OSError, ConnectionError, AttributeError) as e:
            await self.close()
            raise ServerConnectionError(repr(e)) from e

    async def _request(self, kind: str, wanted: str) -> str:
        """Sends one request and waits for its reply. A late reply to an abandoned partial is skipped."""
        timeout = get_settings().STT_SERVER_TIMEOUT_SECONDS
        async with self._lock:
            try:
                await self._ws.send_json({"type": kind})
                while True:
                    reply = await asyncio.wait_for(self._ws.receive_json(), timeout=timeout)
                    if reply.get("type") == wanted:
                        return reply.get("text") or ""
                    if reply.get("type") != "partial":
                        raise ServerDecodeError(reply.get("message") or reply.get("type") or "unexpected reply")
            except asyncio.TimeoutError as e:
                await self.close()  # its reply could still arrive and be taken for the next one
                raise ServerDecodeError(f"no answer within {timeout}s") from e
            except (aiohttp.ClientError, OSError, ConnectionError, TypeError, ValueError, AttributeError) as e:
                await self.close()
                raise ServerConnectionError(repr(e)) from e

    async def final(self) -> str:
        return await self._request("flush", "final")

    async def partial(self) -> str:
        return await self._request("partial", "partial")

    async def close(self) -> None:
        ws, http = self._ws, self._http
        self._ws = self._http = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if http is not None:
            await http.close()


async def decode_on_sarvam(pcm: bytes, sample_rate: int) -> str:
    """Fallback: the same model and mode the live Sarvam stream uses, over REST."""
    from speech.bulbul_tts import shared_http

    s = get_settings()
    trimmed = float_to_pcm16(trim_silence(pcm16_to_float(pcm), sample_rate))  # sent audio is billed
    if not trimmed:
        return ""
    wav = io.BytesIO()
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(trimmed)
    resp = await shared_http().post(s.SARVAM_STT_URL, headers={"api-subscription-key": s.SARVAM_API_KEY},
                                    data={"model": s.SARVAM_STT_MODEL, "mode": s.SARVAM_STT_MODE,
                                          "language_code": s.SARVAM_STT_LANGUAGE_CODE},
                                    files={"file": ("utterance.wav", wav.getvalue(), "audio/wav")})
    resp.raise_for_status()
    return (resp.json().get("transcript") or "").strip()


class LocalSTT(lk_stt.STT):
    def __init__(self, engine: str, *, decoder: Optional[Decoder] = None, fallback: Optional[Decoder] = None):
        super().__init__(capabilities=lk_stt.STTCapabilities(streaming=True, interim_results=False, manual_flush=True))
        self.engine = engine
        self.uses_server = decoder is None
        self._decoder = decoder or (lambda pcm, rate: decode_on_server(engine, pcm, rate))
        self._fallback = fallback or decode_on_sarvam
        self.last_decode_ms: Optional[float] = None

    @property
    def model(self) -> str:
        return self.engine

    @property
    def provider(self) -> str:
        return "local"

    def _fallback_allowed(self) -> bool:
        s = get_settings()
        return s.STT_FALLBACK_TO_SARVAM and s.sarvam_configured

    async def decode(self, pcm: bytes, sample_rate: int, *,
                     streamed: Optional[Callable[[], Awaitable[str]]] = None) -> Decoded:
        """streamed asks the call's open connection, which already has the audio; pcm is kept for a retry or fallback."""
        started = time.monotonic()
        source = self.engine
        try:
            if streamed is None:
                text = await self._decoder(pcm, sample_rate)
            else:
                try:
                    text = await streamed()
                except ServerConnectionError as e:
                    logger.warning(f"STT connection lost ({e}); sending the utterance again on a new connection")
                    text = await self._decoder(pcm, sample_rate)
        except ServerDecodeError as e:
            if not self._fallback_allowed():
                logger.error(f"Local STT failed and no Sarvam fallback is available: {e}")
                text, source = "", "none"
            else:
                logger.warning(f"Local STT failed ({e}); using Sarvam for this utterance")
                source = "sarvam"
                try:
                    text = await self._fallback(pcm, sample_rate)
                except Exception as fallback_error:
                    logger.error(f"Sarvam STT fallback also failed: {fallback_error!r}")
                    text = ""
        elapsed = (time.monotonic() - started) * 1000
        self.last_decode_ms = elapsed
        logger.info(f"STT {source}: {elapsed:.0f} ms for {len(pcm) / 2 / sample_rate:.1f}s of audio")
        return Decoded(text, source, elapsed)

    async def _recognize_impl(self, buffer: AudioBuffer, *, language: NotGivenOr[str] = NOT_GIVEN,
                              conn_options: APIConnectOptions) -> lk_stt.SpeechEvent:
        frame = merge_frames(buffer)
        result = await self.decode(bytes(frame.data), frame.sample_rate)
        return lk_stt.SpeechEvent(type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT, request_id=uuid.uuid4().hex,
                                  alternatives=[lk_stt.SpeechData(language=LANGUAGE, text=result.text)])

    def stream(self, *, language: NotGivenOr[str] = NOT_GIVEN,
               conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "LocalSpeechStream":
        return LocalSpeechStream(stt=self, conn_options=conn_options)

    async def preload(self) -> None:
        """Loads the model ahead of the first utterance, so the first reply isn't slowed by it."""
        s = get_settings()
        try:
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(s.STT_SERVER_URL, headers=auth_headers()) as ws:
                    await ws.send_json({"type": "load", "engine": self.engine})
                    reply = await asyncio.wait_for(ws.receive_json(), timeout=300)
            if reply.get("type") == "error":
                logger.error(f"Local STT engine did not load: {reply.get('message')}")
        except Exception as e:
            logger.warning(f"Local STT server not reachable at {s.STT_SERVER_URL}: {e!r}")


class LocalSpeechStream(lk_stt.SpeechStream):
    def __init__(self, *, stt: LocalSTT, conn_options: APIConnectOptions):
        self._rate = get_settings().SARVAM_STT_SAMPLE_RATE
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=self._rate)
        self._local: LocalSTT = stt
        self._chunks: deque = deque()
        self._audio_bytes = 0
        self._partial_task: Optional[asyncio.Task] = None
        self._streaming = stt.uses_server and get_settings().STT_SERVER_STREAMING
        self._conn: Optional[STTConnection] = None
        self._streamed_whole_utterance = False  # the open connection has had every frame of this utterance
        self._retry_at = 0.0

    def _keep(self, data: bytes) -> None:
        self._chunks.append(data)
        self._audio_bytes += len(data)
        limit = MAX_UTTERANCE_SECONDS * self._rate * 2
        while self._audio_bytes > limit and self._chunks:
            self._audio_bytes -= len(self._chunks.popleft())

    async def _stream_frame(self, data: bytes) -> None:
        """Sends a frame on the call's connection. A connection opened mid-utterance has missed its start,
        so it is only trusted from the next utterance on; until then the utterance is sent whole at the end."""
        starts_utterance = self._audio_bytes == len(data)
        if self._conn is not None and self._conn.open:
            if starts_utterance:
                self._streamed_whole_utterance = True
        else:
            if not starts_utterance or time.monotonic() < self._retry_at:
                self._streamed_whole_utterance = False
                return
            self._conn = self._conn or STTConnection(self._local.engine, self._rate)
            try:
                await self._conn.connect()
            except ServerDecodeError as e:
                logger.warning(f"STT server not reachable for streaming ({e}); sending utterances whole for now")
                self._retry_at = time.monotonic() + RECONNECT_SECONDS
                self._streamed_whole_utterance = False
                return
            self._streamed_whole_utterance = True
        if not self._streamed_whole_utterance:
            return  # this utterance goes whole at the end; the connection is replaced before the next one
        try:
            await self._conn.send_audio(data)
        except ServerDecodeError:
            self._streamed_whole_utterance = False

    async def _finish_utterance(self) -> None:
        if self._partial_task:
            self._partial_task.cancel()
            self._partial_task = None
        if not self._audio_bytes:
            return
        pcm = b"".join(self._chunks)
        self._chunks.clear()
        self._audio_bytes = 0
        conn_open = self._conn is not None and self._conn.open
        streamed = self._conn.final if self._streamed_whole_utterance and conn_open else None
        if conn_open and streamed is None:
            await self._conn.close()  # it holds part of this utterance; the next one starts on a fresh connection
        self._streamed_whole_utterance = False
        result = await self._local.decode(pcm, self._rate, streamed=streamed)
        request_id = uuid.uuid4().hex
        self._event_ch.send_nowait(lk_stt.SpeechEvent(
            type=lk_stt.SpeechEventType.RECOGNITION_USAGE, request_id=request_id,
            recognition_usage=lk_stt.RecognitionUsage(audio_duration=len(pcm) / 2 / self._rate)))
        if result.text:
            self._event_ch.send_nowait(lk_stt.SpeechEvent(
                type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT, request_id=request_id,
                alternatives=[lk_stt.SpeechData(language=LANGUAGE, text=result.text)]))
        self._event_ch.send_nowait(lk_stt.SpeechEvent(type=lk_stt.SpeechEventType.END_OF_SPEECH, request_id=request_id))

    async def _partials(self) -> None:
        """Optional rolling results. They decode the audio so far, so this is off by default on CPU."""
        while True:
            await asyncio.sleep(PARTIAL_INTERVAL_SECONDS)
            if not self._audio_bytes:
                continue
            try:
                if self._streamed_whole_utterance and self._conn is not None and self._conn.open:
                    text = await self._conn.partial()
                else:
                    text = await self._local._decoder(b"".join(self._chunks), self._rate)
            except ServerDecodeError:
                continue
            if text:
                self._event_ch.send_nowait(lk_stt.SpeechEvent(
                    type=lk_stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    alternatives=[lk_stt.SpeechData(language=LANGUAGE, text=text)]))

    async def _run(self) -> None:
        partials_on = get_settings().STT_PARTIALS_ENABLED
        preload = asyncio.create_task(self._local.preload()) if self._local.uses_server else None
        try:
            async for item in self._input_ch:
                if isinstance(item, rtc.AudioFrame):
                    data = bytes(item.data)
                    self._keep(data)
                    if self._streaming:
                        await self._stream_frame(data)
                    if partials_on and self._partial_task is None:
                        self._partial_task = asyncio.create_task(self._partials())
                else:
                    await self._finish_utterance()
            await self._finish_utterance()
        finally:
            if self._partial_task:
                self._partial_task.cancel()
            if preload:
                preload.cancel()
            if self._conn is not None:
                await self._conn.close()
