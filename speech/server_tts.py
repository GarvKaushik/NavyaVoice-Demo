"""Pipeline side of the TTS service (TTS_PROVIDER=server).

Each call keeps one WebSocket open to TTS_SERVER_URL. Every sentence gets its own turn_id; its audio streams back
in pieces at telephony rate. When LiveKit stops a sentence (the customer interrupts), the sentence's stream is
closed and a cancel goes to the service, so it stops generating straight away. Interruption handling itself stays
in the pipeline; the service only follows orders.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from typing import AsyncIterator, Dict, Optional

import aiohttp
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectionError, APIConnectOptions, tts as lk_tts

from config.settings import get_settings
from speech.bulbul_tts import BulbulTTSError, pcm_to_wav
from speech.normalizer import HinglishNormalizer
from speech.service_auth import auth_headers

logger = logging.getLogger(__name__)

TURN_ID_LENGTH = 32
CONNECT_TIMEOUT_SECONDS = 5


class ServerTTSError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable  # a lost connection can be retried; a timeout or a voice failure can't


class TTSConnection:
    """One call's connection. Sentences can overlap; replies are routed by turn_id."""

    def __init__(self, url: Optional[str] = None, sample_rate: Optional[int] = None):
        s = get_settings()
        self.url = url or s.TTS_SERVER_URL
        self.sample_rate = sample_rate or s.TTS_SERVER_SAMPLE_RATE
        self._http: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._reader: Optional[asyncio.Task] = None
        self._turns: Dict[str, asyncio.Queue] = {}
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._background: set = set()
        self.last_engine: Optional[str] = None
        self.last_fallback = False

    @property
    def open(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def _ensure_open(self) -> None:
        async with self._connect_lock:
            if self.open:
                return
            await self._close_socket()
            try:
                self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=CONNECT_TIMEOUT_SECONDS))
                self._ws = await self._http.ws_connect(self.url, max_msg_size=0, headers=auth_headers(), heartbeat=20)
            except (aiohttp.ClientError, OSError, ValueError) as e:
                await self._close_socket()
                raise ServerTTSError(f"TTS server not reachable at {self.url}: {e!r}", retryable=True) from e
            self._reader = asyncio.create_task(self._read(self._ws))

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        reason = "the TTS server closed the connection"
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    queue = self._turns.get(message.data[:TURN_ID_LENGTH].decode("ascii", "replace"))
                    if queue is not None:
                        queue.put_nowait(bytes(message.data[TURN_ID_LENGTH:]))
                elif message.type == aiohttp.WSMsgType.TEXT:
                    reply = json.loads(message.data)
                    queue = self._turns.get(reply.get("turn_id") or "")
                    if queue is not None:
                        queue.put_nowait(reply)
                    elif reply.get("type") == "error":
                        logger.warning(f"TTS server: {reply.get('message')}")
                elif message.type == aiohttp.WSMsgType.ERROR:
                    reason = repr(ws.exception())
                    break
        except Exception as e:
            reason = repr(e)
        finally:
            for queue in self._turns.values():
                queue.put_nowait(ServerTTSError(reason, retryable=True))

    async def _send(self, message: dict) -> None:
        try:
            async with self._send_lock:
                await self._ws.send_json(message)
        except (aiohttp.ClientError, OSError, ConnectionError, AttributeError, RuntimeError) as e:
            raise ServerTTSError(f"sending to the TTS server failed: {e!r}", retryable=True) from e

    def _cancel_later(self, turn_id: str) -> None:
        async def send_cancel() -> None:
            with contextlib.suppress(ServerTTSError):
                await self._send({"type": "cancel", "turn_id": turn_id})

        task = asyncio.create_task(send_cancel())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        """Yields 16-bit mono PCM at self.sample_rate. Closing the iterator early cancels the sentence on the server."""
        await self._ensure_open()
        turn_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        self._turns[turn_id] = queue
        finished = False
        timeout = get_settings().TTS_SERVER_TIMEOUT_SECONDS
        try:
            await self._send({"type": "speak", "turn_id": turn_id, "text": text, "sample_rate": self.sample_rate})
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout)
                except asyncio.TimeoutError as e:
                    raise ServerTTSError(f"no audio from the TTS server within {timeout}s") from e
                if isinstance(item, bytes):
                    yield item
                    continue
                finished = True
                if isinstance(item, Exception):
                    raise item
                kind = item.get("type")
                if kind == "done":
                    self.last_engine, self.last_fallback = item.get("engine"), bool(item.get("fallback"))
                    if self.last_fallback:
                        logger.warning(f"TTS service used its fallback voice {self.last_engine}")
                    return
                if kind == "cancelled":
                    return
                raise ServerTTSError(item.get("message") or f"unexpected reply {kind!r}")
        finally:
            self._turns.pop(turn_id, None)
            if not finished and self.open:
                self._cancel_later(turn_id)

    async def _close_socket(self) -> None:
        reader, ws, http = self._reader, self._ws, self._http
        self._reader = self._ws = self._http = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        if reader is not None:
            reader.cancel()
        if http is not None:
            await http.close()

    async def aclose(self) -> None:
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        await self._close_socket()


class ServerTTS(lk_tts.TTS):
    """LiveKit TTS for live calls. The worker hands over one normalized sentence at a time."""

    def __init__(self, connection: Optional[TTSConnection] = None):
        rate = get_settings().TTS_SERVER_SAMPLE_RATE
        super().__init__(capabilities=lk_tts.TTSCapabilities(streaming=False), sample_rate=rate, num_channels=1)
        self.connection = connection or TTSConnection(sample_rate=rate)

    @property
    def model(self) -> str:
        return get_settings().TTS_SERVER_VOICE

    @property
    def provider(self) -> str:
        return "tts-server"

    @property
    def last_was_fallback(self) -> bool:
        """The last sentence came from the service's fallback voice, so it shouldn't be cached as the main voice."""
        return self.connection.last_fallback

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "ServerTTSStream":
        return ServerTTSStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        await self.connection.aclose()


class ServerTTSStream(lk_tts.ChunkedStream):
    async def _run(self, output_emitter: lk_tts.AudioEmitter) -> None:
        output_emitter.initialize(request_id=uuid.uuid4().hex, sample_rate=self._tts.sample_rate, num_channels=1,
                                  mime_type="audio/pcm")
        try:
            async with contextlib.aclosing(self._tts.connection.speak(self.input_text)) as audio:
                async for pcm in audio:
                    output_emitter.push(pcm)
        except ServerTTSError as e:
            raise APIConnectionError(str(e), retryable=e.retryable) from e
        output_emitter.flush()


class ServerTTSClient:
    """Same interface as BulbulTTSClient, for the script console and pre-rendered phrases."""

    def __init__(self):
        s = get_settings()
        self.model = "tts-server"
        self.voice_id = s.TTS_SERVER_VOICE
        self.sample_rate = s.TTS_SERVER_SAMPLE_RATE
        self.last_error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(get_settings().TTS_SERVER_URL)

    async def synthesize_pcm(self, text: str, *, normalize: bool = True) -> bytes:
        spoken = HinglishNormalizer.normalize(text) if normalize else text
        if not spoken:
            raise BulbulTTSError("empty text")
        connection = TTSConnection(sample_rate=self.sample_rate)
        pcm = bytearray()
        try:
            async with contextlib.aclosing(connection.speak(spoken)) as audio:
                async for piece in audio:
                    pcm += piece
        except ServerTTSError as e:
            self.last_error = str(e)
            raise BulbulTTSError(self.last_error) from e
        finally:
            await connection.aclose()
        return bytes(pcm)

    async def synthesize_wav(self, text: str, *, normalize: bool = True) -> bytes:
        return pcm_to_wav(await self.synthesize_pcm(text, normalize=normalize), self.sample_rate)
