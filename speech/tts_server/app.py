"""TTS service. One WebSocket per call; sentences are queued and spoken in order.

Protocol:
    client -> {"type": "speak", "turn_id": "<32 hex characters>", "text": "...", "sample_rate": 8000}
    client -> {"type": "cancel", "turn_id": "..."}     stops that sentence now, or drops it if still queued
    client -> {"type": "load"}                         loads the main voice ahead of the first sentence
    server -> binary: the 32-character turn_id, then 16-bit mono PCM at the requested rate (many per sentence)
    server -> {"type": "done", "turn_id": "...", "engine": "maya", "fallback": false,
               "first_audio_ms": 140.0, "total_ms": 900.0}
              {"type": "cancelled", "turn_id": "..."}, {"type": "error", "turn_id": "...", "message": "..."}
              {"type": "ready", "engine": "maya", "load_ms": 0.0}

The text arrives normalized (numbers and symbols already spelled out); each voice converts it as it needs.
When the main voice fails before any of a sentence's audio is sent, the fallback voice speaks that sentence.
With SPEECH_SERVER_TOKEN set, the socket needs the header "Authorization: Bearer <token>".
"""

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Dict, List, Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from config.settings import get_settings
from speech.service_auth import authorized
from speech.tts_server.audio import StreamResampler, float_to_pcm16, to_float
from speech.tts_server.engines import EngineUnavailable, TTSEngine, create_engine

logger = logging.getLogger(__name__)

TURN_ID_LENGTH = 32
_DONE = object()


class EngineHost:
    """Loads each voice once, on first use, and limits how many sentences each one makes at a time."""

    def __init__(self, factory: Callable[[str], TTSEngine] = create_engine):
        self.factory = factory
        self.engines: Dict[str, TTSEngine] = {}
        self.seats: Dict[str, asyncio.Semaphore] = {}
        self.loading: Dict[str, asyncio.Lock] = {}
        self.active = 0

    @staticmethod
    def chain() -> List[str]:
        s = get_settings()
        names = [s.TTS_ENGINE]
        if s.TTS_FALLBACK_ENGINE and s.TTS_FALLBACK_ENGINE != s.TTS_ENGINE:
            names.append(s.TTS_FALLBACK_ENGINE)
        return names

    async def get(self, name: str) -> TTSEngine:
        if name in self.engines:
            return self.engines[name]
        async with self.loading.setdefault(name, asyncio.Lock()):
            if name not in self.engines:
                started = time.monotonic()
                engine = self.factory(name)
                await asyncio.to_thread(engine.load)
                self.seats[name] = asyncio.Semaphore(max(1, engine.streams))
                self.engines[name] = engine
                logger.info(f"Loaded TTS voice {name} in {(time.monotonic() - started) * 1000:.0f} ms")
        return self.engines[name]

    async def _generate(self, engine: TTSEngine, text: str, rate: int,
                        first_audio_timeout: Optional[float]) -> AsyncIterator[bytes]:
        """Runs the voice in a worker thread and yields PCM at the requested rate. Closing it stops the voice."""
        seat = self.seats[engine.name]
        await seat.acquire()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def put(item) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass  # the event loop has closed

        def work() -> None:
            chunks = None
            try:
                chunks = engine.stream(text, stop)
                for chunk in chunks:
                    if stop.is_set():
                        break
                    put(chunk)
            except BaseException as e:
                put(e)
            finally:
                close = getattr(chunks, "close", None)
                if close:
                    try:
                        close()  # lets the voice end its own generation now, not when the generator is collected
                    except Exception:
                        pass
                put(_DONE)

        # The seat frees when the voice has really stopped, not when this sentence is abandoned
        loop.run_in_executor(None, work).add_done_callback(lambda _: seat.release())
        resampler = StreamResampler(engine.sample_rate, rate)
        waiting_for_first = True
        try:
            while True:
                if waiting_for_first and first_audio_timeout:
                    item = await asyncio.wait_for(queue.get(), first_audio_timeout)
                else:
                    item = await queue.get()
                waiting_for_first = False
                if item is _DONE:
                    break
                if isinstance(item, BaseException):
                    raise item
                audio = resampler.push(to_float(item))
                if audio.size:
                    yield float_to_pcm16(audio)
            tail = resampler.push(np.zeros(0, np.float32), last=True)
            if tail.size:
                yield float_to_pcm16(tail)
        finally:
            stop.set()

    async def speak(self, text: str, rate: int, emit: Callable[[bytes], Awaitable[None]]) -> dict:
        """Speaks one sentence through emit. Moves to the next voice only while nothing has been sent yet."""
        names = self.chain()
        errors = []
        self.active += 1
        try:
            for index, name in enumerate(names):
                last = index == len(names) - 1
                try:
                    engine = await self.get(name)
                except Exception as e:
                    errors.append(f"{name}: {e}")
                    logger.error(f"TTS voice {name} could not load: {e}")
                    continue
                sent = False
                timeout = None if last else get_settings().TTS_ENGINE_FIRST_AUDIO_TIMEOUT_SECONDS
                generator = self._generate(engine, text, rate, timeout)
                try:
                    async for pcm in generator:
                        sent = True
                        await emit(pcm)
                    return {"engine": name, "fallback": index > 0}
                except asyncio.TimeoutError:
                    if sent:
                        raise
                    errors.append(f"{name}: no audio within {timeout}s")
                    logger.warning(f"TTS voice {name} gave no audio within {timeout}s; trying the next one")
                except Exception as e:
                    if sent:
                        raise  # half a sentence can't be finished in another voice
                    errors.append(f"{name}: {e!r}")
                    logger.warning(f"TTS voice {name} failed ({e!r}); trying the next one")
                finally:
                    await generator.aclose()
            raise EngineUnavailable("; ".join(errors) or "no TTS voice configured")
        finally:
            self.active -= 1

    def close(self) -> None:
        for engine in self.engines.values():
            try:
                engine.close()
            except Exception as e:
                logger.warning(f"Closing TTS voice {engine.name} failed: {e!r}")
        self.engines.clear()


host = EngineHost()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    preload = None
    if get_settings().TTS_PRELOAD:
        preload = asyncio.create_task(_preload())
    yield
    if preload:
        preload.cancel()
    host.close()


async def _preload() -> None:
    name = get_settings().TTS_ENGINE
    try:
        await host.get(name)
    except Exception as e:
        logger.error(f"TTS voice {name} did not load at start-up: {e}")


app = FastAPI(title="TTS service", lifespan=lifespan)


@app.get("/health")
async def health():
    s = get_settings()
    return {"status": "ok", "main": s.TTS_ENGINE, "fallback": s.TTS_FALLBACK_ENGINE or None,
            "loaded": sorted(host.engines), "speaking": host.active}


class Connection:
    """One call's socket: a queue of sentences, spoken one after another, any of which can be cancelled."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue = asyncio.Queue()
        self.queued: set = set()
        self.dropped: set = set()
        self.current_id: Optional[str] = None
        self.current_task: Optional[asyncio.Task] = None
        self.send_lock = asyncio.Lock()

    async def send_json(self, message: dict) -> None:
        async with self.send_lock:
            await self.ws.send_json(message)

    async def send_audio(self, turn_id: str, pcm: bytes) -> None:
        async with self.send_lock:
            await self.ws.send_bytes(turn_id.encode("ascii") + pcm)

    def cancel(self, turn_id: str) -> None:
        if turn_id == self.current_id and self.current_task and not self.current_task.done():
            self.current_task.cancel()
        elif turn_id in self.queued:
            self.dropped.add(turn_id)

    async def _speak(self, turn_id: str, text: str, rate: int) -> dict:
        started = time.monotonic()
        first: List[float] = []

        async def emit(pcm: bytes) -> None:
            if not first:
                first.append((time.monotonic() - started) * 1000)
            await self.send_audio(turn_id, pcm)

        info = await host.speak(text, rate, emit)
        return {"type": "done", "turn_id": turn_id, **info, "first_audio_ms": round(first[0], 1) if first else None,
                "total_ms": round((time.monotonic() - started) * 1000, 1)}

    async def run(self) -> None:
        while True:
            turn_id, text, rate = await self.queue.get()
            self.queued.discard(turn_id)
            if turn_id in self.dropped:
                self.dropped.discard(turn_id)
                await self.send_json({"type": "cancelled", "turn_id": turn_id})
                continue
            self.current_id = turn_id
            self.current_task = asyncio.create_task(self._speak(turn_id, text, rate))
            await asyncio.wait([self.current_task])
            task, self.current_id, self.current_task = self.current_task, None, None
            if task.cancelled():
                await self.send_json({"type": "cancelled", "turn_id": turn_id})
            elif task.exception() is not None:
                error = task.exception()
                message = str(error) if isinstance(error, EngineUnavailable) else repr(error)[:300]
                logger.error(f"TTS sentence failed: {message}")
                await self.send_json({"type": "error", "turn_id": turn_id, "message": message})
            else:
                await self.send_json(task.result())

    def close(self) -> None:
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()


@app.websocket("/v1/tts")
async def tts_socket(ws: WebSocket) -> None:
    if not authorized(ws.headers):
        await ws.close(code=1008)
        return
    await ws.accept()
    connection = Connection(ws)
    runner = asyncio.create_task(connection.run())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            try:
                control = json.loads(message.get("text") or "")
            except ValueError:
                await connection.send_json({"type": "error", "message": "expected a JSON message"})
                continue
            kind = control.get("type")
            turn_id = str(control.get("turn_id") or "")
            if kind == "speak":
                if len(turn_id) != TURN_ID_LENGTH or not turn_id.isascii() or not control.get("text"):
                    await connection.send_json({"type": "error", "turn_id": turn_id,
                                                "message": f"speak needs text and a {TURN_ID_LENGTH}-character turn_id"})
                    continue
                rate = int(control.get("sample_rate") or get_settings().TTS_SERVER_SAMPLE_RATE)
                connection.queued.add(turn_id)
                await connection.queue.put((turn_id, str(control["text"]), rate))
            elif kind == "cancel":
                connection.cancel(turn_id)
            elif kind == "load":
                started = time.monotonic()
                name = get_settings().TTS_ENGINE
                try:
                    await host.get(name)
                    await connection.send_json({"type": "ready", "engine": name,
                                                "load_ms": round((time.monotonic() - started) * 1000, 1)})
                except Exception as e:
                    await connection.send_json({"type": "error", "message": f"{name}: {e}"})
            else:
                await connection.send_json({"type": "error", "message": f"unknown message type {kind!r}"})
    except WebSocketDisconnect:
        return
    finally:
        runner.cancel()
        connection.close()
