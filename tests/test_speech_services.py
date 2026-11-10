"""STT and TTS as separate services, with fake voices and models: no GPU, no downloads, no outside network."""

import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager

import numpy as np
import pytest
import uvicorn
from fastapi.testclient import TestClient
from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, stt as lk_stt
from starlette.websockets import WebSocketDisconnect

from config.settings import get_settings
from speech.stt_server import app as stt_app
from speech.stt_server.audio import float_to_pcm16
from speech.stt_server.engines import Engine, onnx_providers
from speech.tts_server import app as tts_app
from speech.tts_server.audio import StreamResampler, speed_up
from speech.tts_server.engines import EngineUnavailable, TTSEngine, create_engine, svara_text


def _set(monkeypatch, **values):
    s = get_settings()
    for key, value in values.items():
        monkeypatch.setattr(s, key, value)
    return s


@contextmanager
def serve(app):
    """Runs an app on a real local port, so the pipeline's aiohttp clients can be tested end to end."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(5)


def _speech(rate=8000, seconds=1.0):
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    audio = np.concatenate([np.zeros(rate // 4), 0.3 * np.sin(2 * np.pi * 220 * t), np.zeros(rate // 4)]).astype(np.float32)
    return float_to_pcm16(audio)


# ------------------------------------------------------------------------------
# Shared secret
# ------------------------------------------------------------------------------
def test_stt_server_rejects_a_missing_or_wrong_token(monkeypatch, stt_fakes):
    _set(monkeypatch, SPEECH_SERVER_TOKEN="s3cret")
    client = TestClient(stt_app.app)
    for headers in ({}, {"Authorization": "Bearer nope"}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/stt", headers=headers) as ws:
                ws.receive_json()
    with client.websocket_connect("/v1/stt", headers={"Authorization": "Bearer s3cret"}) as ws:
        ws.send_json({"type": "config", "engine": "whisper_hinglish", "sample_rate": 8000})
        ws.send_bytes(_speech())
        ws.send_json({"type": "flush"})
        assert ws.receive_json()["type"] == "final"


def test_tts_server_rejects_a_missing_token(monkeypatch, voices):
    _set(monkeypatch, SPEECH_SERVER_TOKEN="s3cret")
    with pytest.raises(WebSocketDisconnect):
        with TestClient(tts_app.app).websocket_connect("/v1/tts") as ws:
            ws.receive_json()


def test_empty_token_means_no_check(monkeypatch):
    from speech.service_auth import auth_headers, authorized

    _set(monkeypatch, SPEECH_SERVER_TOKEN="")
    assert auth_headers() == {} and authorized({})
    _set(monkeypatch, SPEECH_SERVER_TOKEN="abc")
    assert auth_headers() == {"Authorization": "Bearer abc"}
    assert authorized({"authorization": "Bearer abc"}) and not authorized({"authorization": "Bearer abd"})


# ------------------------------------------------------------------------------
# STT service
# ------------------------------------------------------------------------------
class FakeSTT(Engine):
    def __init__(self, name):
        self.name = name
        self.decoded = []

    def load(self):
        pass

    def transcribe(self, audio):
        self.decoded.append(audio.size)
        return f"heard {len(self.decoded)}"


@pytest.fixture
def stt_fakes(monkeypatch):
    built = []

    def factory(name):
        built.append(FakeSTT(name))
        return built[-1]

    monkeypatch.setattr(stt_app, "host", stt_app.EngineHost(factory))
    _set(monkeypatch, SPEECH_SERVER_TOKEN="", STT_PROVIDER="whisper_hinglish")
    return built


def test_one_stt_connection_serves_partials_and_many_utterances(stt_fakes):
    with TestClient(stt_app.app).websocket_connect("/v1/stt") as ws:
        ws.send_json({"type": "config", "engine": "whisper_hinglish", "sample_rate": 8000})
        ws.send_bytes(_speech(seconds=0.5))
        ws.send_json({"type": "partial"})
        assert ws.receive_json() == {"type": "partial", "text": "heard 1"}
        ws.send_bytes(_speech(seconds=0.5))
        ws.send_json({"type": "flush"})
        first = ws.receive_json()
        assert first["type"] == "final" and first["text"] == "heard 2"
        assert stt_fakes[0].decoded[1] > stt_fakes[0].decoded[0]  # the partial kept its audio for the final
        ws.send_bytes(_speech())
        ws.send_json({"type": "flush"})
        assert ws.receive_json()["text"] == "heard 3"


def test_indicconformer_uses_the_gpu_only_when_asked_and_available():
    assert onnx_providers("cpu", ["CUDAExecutionProvider", "CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert onnx_providers("cuda", ["CUDAExecutionProvider", "CPUExecutionProvider"])[0] == "CUDAExecutionProvider"
    assert onnx_providers("cuda", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def _frame(pcm: bytes, rate=8000):
    return rtc.AudioFrame(data=pcm, sample_rate=rate, num_channels=1, samples_per_channel=len(pcm) // 2)


async def _two_utterances(stt):
    stream = stt.stream()
    for _ in range(2):
        speech = _speech()
        for i in range(0, len(speech), 320):  # 20 ms frames, as LiveKit sends them
            stream.push_frame(_frame(speech[i:i + 320]))
        stream.flush()
    stream.end_input()
    events = [event async for event in stream]
    return [e.alternatives[0].text for e in events if e.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT]


async def test_pipeline_streams_a_whole_call_over_one_stt_connection(monkeypatch, stt_fakes):
    from speech import local_stt

    async def per_utterance(*args):
        raise AssertionError("streaming should not open a connection per utterance")

    monkeypatch.setattr(local_stt, "decode_on_server", per_utterance)
    with serve(stt_app.app) as port:
        _set(monkeypatch, STT_SERVER_URL=f"ws://127.0.0.1:{port}/v1/stt", STT_SERVER_STREAMING=True,
             SPEECH_SERVER_TOKEN="tok")
        assert await _two_utterances(local_stt.LocalSTT("whisper_hinglish")) == ["heard 1", "heard 2"]


async def test_pipeline_sends_utterances_whole_when_streaming_cannot_connect(monkeypatch):
    from speech import local_stt

    sent = []

    async def per_utterance(engine, pcm, rate):
        sent.append(len(pcm))
        return "whole"

    monkeypatch.setattr(local_stt, "decode_on_server", per_utterance)
    _set(monkeypatch, STT_SERVER_URL="ws://127.0.0.1:1/v1/stt", STT_SERVER_STREAMING=True)
    assert await _two_utterances(local_stt.LocalSTT("whisper_hinglish")) == ["whole", "whole"]
    assert len(sent) == 2 and sent[0] > 0


# ------------------------------------------------------------------------------
# TTS service
# ------------------------------------------------------------------------------
class FakeVoice(TTSEngine):
    def __init__(self, name, fail_load=False, fail_after=None, first_delay=0.0, endless=False, chunks=3):
        self.name = name
        self.sample_rate = 24000
        self.fail_load, self.fail_after, self.first_delay = fail_load, fail_after, first_delay
        self.endless, self.chunks = endless, chunks
        self.texts = []
        self.stopped = threading.Event()

    def load(self):
        if self.fail_load:
            raise EngineUnavailable(f"{self.name} is not installed")

    def stream(self, text, stop):
        self.texts.append(text)
        try:
            if self.first_delay:
                stop.wait(self.first_delay)
            i = 0
            while self.endless or i < self.chunks:
                if stop.is_set():
                    return
                if self.fail_after is not None and i == self.fail_after:
                    raise RuntimeError("voice crashed")
                yield np.full(2400, 0.1, np.float32)  # 100 ms at 24 kHz
                i += 1
                if self.endless:
                    time.sleep(0.02)
        finally:
            if stop.is_set():
                self.stopped.set()


@pytest.fixture
def voices(monkeypatch):
    specs, made = {}, {}

    def factory(name):
        made[name] = FakeVoice(name, **specs.get(name, {}))
        return made[name]

    monkeypatch.setattr(tts_app, "host", tts_app.EngineHost(factory))
    _set(monkeypatch, SPEECH_SERVER_TOKEN="", TTS_ENGINE="maya", TTS_FALLBACK_ENGINE="svara",
         TTS_ENGINE_FIRST_AUDIO_TIMEOUT_SECONDS=0.5, TTS_SERVER_SAMPLE_RATE=8000)
    return specs, made


def _turn(n: int) -> str:
    return f"{n:032x}"


def _until_reply(ws, turn_id):
    audio = bytearray()
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            assert message["bytes"][:32].decode() == turn_id
            audio += message["bytes"][32:]
            continue
        reply = json.loads(message["text"])
        if reply.get("turn_id") == turn_id:
            return bytes(audio), reply


def test_tts_streams_telephony_audio_tagged_with_its_turn(voices):
    specs, made = voices
    with TestClient(tts_app.app).websocket_connect("/v1/tts") as ws:
        ws.send_json({"type": "speak", "turn_id": _turn(1), "text": "Namaste ji", "sample_rate": 8000})
        audio, reply = _until_reply(ws, _turn(1))
    assert reply["type"] == "done" and reply["engine"] == "maya" and reply["fallback"] is False
    assert abs(len(audio) / 2 - 3 * 800) <= 80  # 300 ms at 24 kHz became 300 ms at 8 kHz
    assert reply["first_audio_ms"] is not None and made["maya"].texts == ["Namaste ji"]
    assert "svara" not in made  # the fallback loads only when needed


@pytest.mark.parametrize("main", [{"fail_load": True}, {"fail_after": 0}, {"first_delay": 3.0}])
def test_tts_switches_to_the_fallback_voice_before_any_audio(voices, main):
    specs, made = voices
    specs["maya"] = main
    with TestClient(tts_app.app).websocket_connect("/v1/tts") as ws:
        ws.send_json({"type": "speak", "turn_id": _turn(2), "text": "Haan ji"})
        audio, reply = _until_reply(ws, _turn(2))
    assert reply == {**reply, "type": "done", "engine": "svara", "fallback": True} and len(audio) > 0


def test_tts_does_not_finish_half_a_sentence_in_another_voice(voices):
    specs, made = voices
    specs["maya"] = {"fail_after": 1}
    with TestClient(tts_app.app).websocket_connect("/v1/tts") as ws:
        ws.send_json({"type": "speak", "turn_id": _turn(3), "text": "Haan ji"})
        audio, reply = _until_reply(ws, _turn(3))
    assert reply["type"] == "error" and "crashed" in reply["message"] and len(audio) > 0
    assert "svara" not in made


def test_tts_reports_when_no_voice_can_speak(voices):
    specs, _ = voices
    specs["maya"] = specs["svara"] = {"fail_load": True}
    with TestClient(tts_app.app).websocket_connect("/v1/tts") as ws:
        ws.send_json({"type": "speak", "turn_id": _turn(4), "text": "Haan ji"})
        _, reply = _until_reply(ws, _turn(4))
    assert reply["type"] == "error" and "maya" in reply["message"] and "svara" in reply["message"]


def test_cancel_stops_the_voice_and_drops_queued_sentences(voices):
    specs, made = voices
    specs["maya"] = {"endless": True}
    with TestClient(tts_app.app).websocket_connect("/v1/tts") as ws:
        ws.send_json({"type": "speak", "turn_id": _turn(5), "text": "a long sentence"})
        ws.send_json({"type": "speak", "turn_id": _turn(6), "text": "queued"})
        assert ws.receive()["bytes"][:32].decode() == _turn(5)  # it started speaking
        ws.send_json({"type": "cancel", "turn_id": _turn(6)})
        ws.send_json({"type": "cancel", "turn_id": _turn(5)})
        _, first = _until_reply(ws, _turn(5))
        _, second = _until_reply(ws, _turn(6))
    assert first["type"] == "cancelled" and second["type"] == "cancelled"
    assert made["maya"].stopped.wait(2) and made["maya"].texts == ["a long sentence"]


def test_tts_rejects_malformed_requests(voices):
    with TestClient(tts_app.app).websocket_connect("/v1/tts") as ws:
        ws.send_json({"type": "speak", "turn_id": "short", "text": "x"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "dance"})
        assert "unknown" in ws.receive_json()["message"]


def test_tts_health_and_preload(voices):
    client = TestClient(tts_app.app)
    with client.websocket_connect("/v1/tts") as ws:
        ws.send_json({"type": "load"})
        assert ws.receive_json()["type"] == "ready"
    assert client.get("/health").json() == {"status": "ok", "main": "maya", "fallback": "svara",
                                            "loaded": ["maya"], "speaking": 0}


def test_engine_chain_and_unknown_engine(monkeypatch):
    _set(monkeypatch, TTS_ENGINE="maya", TTS_FALLBACK_ENGINE="maya")
    assert tts_app.EngineHost.chain() == ["maya"]
    _set(monkeypatch, TTS_FALLBACK_ENGINE="")
    assert tts_app.EngineHost.chain() == ["maya"]
    with pytest.raises(EngineUnavailable):
        create_engine("nope")


def test_real_voices_say_what_is_missing_instead_of_crashing(monkeypatch, tmp_path):
    _set(monkeypatch, TTS_SVARA_GGUF=str(tmp_path / "missing.gguf"), TTS_SVARA_GGUF_REPO="")
    for name in ("maya", "svara"):
        try:
            create_engine(name).load()
        except EngineUnavailable as e:
            assert str(e)  # this laptop has no GPU stack, so this is the expected path
        except ImportError:
            pytest.fail(f"{name} should turn a missing package into EngineUnavailable")


# ------------------------------------------------------------------------------
# Voice preparation
# ------------------------------------------------------------------------------
def test_svara_recipe():
    text = svara_text("Namaste Rajesh ji. Aap kaise hain?")
    assert text.endswith(" <happy>") and "।" in text and "." not in text
    assert not any("a" <= ch.lower() <= "z" for ch in text.replace("<happy>", ""))


def test_speed_up_keeps_pitch_and_shortens_by_the_factor():
    rate = 24000
    t = np.arange(rate * 2) / rate
    tone = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    faster = speed_up(tone, rate, 1.1)
    crossings = lambda x: np.sum(np.diff(np.sign(x)) != 0) / (len(x) / rate) / 2
    assert abs(len(tone) / len(faster) - 1.1) < 0.01
    assert abs(crossings(faster) - 220) < 3
    assert speed_up(tone, rate, 1.0) is tone


def test_resampling_in_pieces_matches_the_rate():
    resampler = StreamResampler(24000, 8000)
    total = sum(resampler.push(np.zeros(2400, np.float32)).size for _ in range(10))
    total += resampler.push(np.zeros(0, np.float32), last=True).size
    assert total == 8000


# ------------------------------------------------------------------------------
# Pipeline client
# ------------------------------------------------------------------------------
async def _synthesize(tts, text="Namaste ji"):
    frames = [ev.frame async for ev in tts.synthesize(text, conn_options=APIConnectOptions(max_retry=0))]
    return b"".join(bytes(f.data) for f in frames), frames


async def test_pipeline_speaks_through_the_tts_service(monkeypatch, voices):
    from speech.server_tts import ServerTTS

    specs, made = voices
    with serve(tts_app.app) as port:
        _set(monkeypatch, TTS_SERVER_URL=f"ws://127.0.0.1:{port}/v1/tts", SPEECH_SERVER_TOKEN="tok")
        tts = ServerTTS()
        try:
            first, frames = await _synthesize(tts)
            second, _ = await _synthesize(tts, "Theek hai")
        finally:
            await tts.aclose()
    assert frames[0].sample_rate == 8000 and len(first) > 0 and len(second) > 0
    assert made["maya"].texts == ["Namaste ji", "Theek hai"] and tts.last_was_fallback is False


async def test_closing_a_sentence_early_cancels_it_on_the_service(monkeypatch, voices):
    from speech.server_tts import TTSConnection

    specs, made = voices
    specs["maya"] = {"endless": True}
    with serve(tts_app.app) as port:
        _set(monkeypatch, TTS_SERVER_URL=f"ws://127.0.0.1:{port}/v1/tts")
        connection = TTSConnection()
        try:
            speech = connection.speak("a long sentence")
            assert len(await speech.__anext__()) > 0
            await speech.aclose()  # what an interruption does
            stopped = await asyncio.to_thread(made["maya"].stopped.wait, 3)
        finally:
            await connection.aclose()
    assert stopped


async def test_fallback_voice_is_reported_so_it_is_not_cached(monkeypatch, voices):
    from speech.server_tts import ServerTTS

    specs, _ = voices
    specs["maya"] = {"fail_load": True}
    with serve(tts_app.app) as port:
        _set(monkeypatch, TTS_SERVER_URL=f"ws://127.0.0.1:{port}/v1/tts")
        tts = ServerTTS()
        try:
            audio, _ = await _synthesize(tts)
        finally:
            await tts.aclose()
    assert len(audio) > 0 and tts.last_was_fallback is True


async def test_unreachable_tts_service_is_a_livekit_connection_error(monkeypatch):
    from speech.server_tts import ServerTTS

    _set(monkeypatch, TTS_SERVER_URL="ws://127.0.0.1:1/v1/tts")
    tts = ServerTTS()
    with pytest.raises(APIConnectionError):
        await _synthesize(tts)
    await tts.aclose()


async def test_console_client_returns_a_wav(monkeypatch, voices):
    from speech.openai_tts import create_tts_client
    from speech.server_tts import ServerTTSClient

    with serve(tts_app.app) as port:
        _set(monkeypatch, TTS_PROVIDER="server", TTS_SERVER_URL=f"ws://127.0.0.1:{port}/v1/tts")
        client = create_tts_client()
        assert isinstance(client, ServerTTSClient)
        wav = await client.synthesize_wav("Namaste ji")
    assert wav[:4] == b"RIFF" and len(wav) > 44


# ------------------------------------------------------------------------------
# Pipeline settings
# ------------------------------------------------------------------------------
def test_server_voice_settings_and_cost(monkeypatch):
    from observability.metrics import CostCalculator

    s = _set(monkeypatch, TTS_PROVIDER="server", TTS_SERVER_VOICE="maya-ahana", TTS_SERVER_SAMPLE_RATE=8000)
    assert (s.tts_voice, s.tts_sample_rate, s.tts_model) == ("maya-ahana", 8000, "tts-server")
    assert CostCalculator.tts_rate_per_10k() == 0.0


def test_worker_builds_the_service_tts_with_an_optional_bulbul_fallback(monkeypatch):
    from livekit.agents import tts as lk_tts
    from livekit.plugins import sarvam

    from agent import worker
    from speech.server_tts import ServerTTS

    monkeypatch.setattr(worker, "sarvam", sarvam, raising=False)
    _set(monkeypatch, TTS_PROVIDER="server", TTS_FALLBACK_TO_SARVAM=False, SARVAM_API_KEY="real-key")
    assert isinstance(worker.build_tts(), ServerTTS)
    _set(monkeypatch, TTS_FALLBACK_TO_SARVAM=True)
    assert isinstance(worker.build_tts(), lk_tts.FallbackAdapter)
    _set(monkeypatch, SARVAM_API_KEY="mock-key")
    assert isinstance(worker.build_tts(), ServerTTS)  # no real key: no fallback
