"""Local STT server. One model is loaded at a time, on first use, because memory is tight on a laptop.

Protocol, one WebSocket per call (or per utterance; both work):
    client -> {"type": "config", "engine": "...", "sample_rate": 8000}, or {"type": "load", "engine": "..."}
    client -> binary 16-bit mono PCM (any number of messages)
    client -> {"type": "partial"}   decodes the utterance so far and keeps it
    client -> {"type": "flush"}     decodes the utterance and starts the next one
    server -> {"type": "final", "text": "...", "decode_ms": 120.0, "audio_seconds": 1.4}
              {"type": "partial", "text": "..."}, {"type": "ready", "load_ms": 0.0},
              {"type": "busy"} or {"type": "error", "message": "..."}

With SPEECH_SERVER_TOKEN set, the socket needs the header "Authorization: Bearer <token>".
"""

import asyncio
import gc
import logging
import time
from typing import Callable, Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from config.settings import get_settings
from speech.service_auth import authorized
from speech.stt_server.audio import TARGET_RATE, pcm16_to_float, resample, trim_silence
from speech.stt_server.engines import Engine, EngineUnavailable, create_engine

logger = logging.getLogger(__name__)
app = FastAPI(title="Local STT server")
MAX_UTTERANCE_SECONDS = 30


class EngineHost:
    """Owns the single loaded model and runs decodes one after another."""

    def __init__(self, factory: Callable[[str], Engine] = create_engine):
        self.factory = factory
        self.engine: Optional[Engine] = None
        self.lock = asyncio.Lock()
        self.waiting = 0

    def _switch(self, name: str) -> float:
        if self.engine is not None and self.engine.name == name:
            return 0.0
        if self.engine is not None:
            self.engine.close()
            self.engine = None
            gc.collect()
        started = time.monotonic()
        engine = self.factory(name)
        engine.load()
        self.engine = engine
        load_ms = (time.monotonic() - started) * 1000
        logger.info(f"Loaded STT engine {name} in {load_ms:.0f} ms")
        return load_ms

    async def load(self, name: str) -> float:
        async with self.lock:
            return await asyncio.to_thread(self._switch, name)

    @property
    def busy(self) -> bool:
        return self.waiting >= get_settings().STT_SERVER_MAX_QUEUE

    async def decode(self, name: str, audio: np.ndarray) -> str:
        self.waiting += 1
        try:
            async with self.lock:
                await asyncio.to_thread(self._switch, name)
                return await asyncio.to_thread(self.engine.transcribe, audio)
        finally:
            self.waiting -= 1


host = EngineHost()


@app.get("/health")
async def health():
    return {"status": "ok", "engine": host.engine.name if host.engine else None, "waiting": host.waiting}


@app.websocket("/v1/stt")
async def stt_socket(ws: WebSocket) -> None:
    if not authorized(ws.headers):
        await ws.close(code=1008)
        return
    await ws.accept()
    settings = get_settings()
    engine = settings.STT_PROVIDER
    sample_rate = settings.SARVAM_STT_SAMPLE_RATE
    pcm = bytearray()
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                pcm += message["bytes"]
                limit = MAX_UTTERANCE_SECONDS * sample_rate * 2
                if len(pcm) > limit:  # a streamed call sends silence too; only the latest audio can matter
                    del pcm[:len(pcm) - limit]
                continue
            import json

            control = json.loads(message.get("text") or "{}")
            kind = control.get("type")
            engine = control.get("engine", engine)
            sample_rate = int(control.get("sample_rate", sample_rate))
            if kind == "load":
                await ws.send_json({"type": "ready", "load_ms": await host.load(engine)})
            elif kind == "flush":
                await ws.send_json(await _decode(engine, bytes(pcm), sample_rate))
                pcm.clear()
            elif kind == "partial":
                reply = await _decode(engine, bytes(pcm), sample_rate)
                if reply["type"] == "final":
                    reply = {"type": "partial", "text": reply["text"]}
                await ws.send_json(reply)
    except WebSocketDisconnect:
        return
    except EngineUnavailable as e:
        logger.error(f"STT engine unavailable: {e}")
        await ws.send_json({"type": "error", "message": str(e)})
    except Exception as e:
        logger.exception("STT request failed")
        await ws.send_json({"type": "error", "message": repr(e)[:300]})


async def _decode(engine: str, pcm: bytes, sample_rate: int) -> dict:
    if host.busy:
        return {"type": "busy"}
    audio = trim_silence(resample(pcm16_to_float(pcm), sample_rate, TARGET_RATE), TARGET_RATE)
    if audio.size == 0:
        return {"type": "final", "text": "", "decode_ms": 0.0, "audio_seconds": 0.0}
    started = time.monotonic()
    text = await host.decode(engine, audio)
    decode_ms = round((time.monotonic() - started) * 1000, 1)
    seconds = round(audio.size / TARGET_RATE, 3)
    logger.info(f"{engine}: {seconds:.1f}s of speech decoded in {decode_ms:.0f} ms, {len(text.split())} words")
    return {"type": "final", "text": text, "decode_ms": decode_ms, "audio_seconds": seconds}
