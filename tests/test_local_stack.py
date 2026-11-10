"""Local demo stack, with fakes only: no network and no model downloads."""

import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from livekit import rtc
from livekit.agents import stt as lk_stt
from pydantic import ValidationError

from config.settings import Settings, get_settings
from guardrails.fast_path_guard import FastPathGuard
from llm.client import LLMClient, LLMEndpoint, build_endpoint, default_reasoning_effort, is_reasoning_model, realtime_endpoints
from speech.lexicon import apply_lexicon
from speech.local_stt import LocalSTT, ServerDecodeError
from speech.stt_server import app as server
from speech.stt_server.audio import float_to_pcm16, resample, trim_silence
from speech.stt_server.engines import Engine, EngineUnavailable, IndicConformerEngine, create_engine
from speech.transliterate import HinglishTransliterator, to_roman


# ------------------------------------------------------------------------------
# Provider selection
# ------------------------------------------------------------------------------
def test_defaults_need_only_openai():
    fields = Settings.model_fields
    assert fields["STT_PROVIDER"].default == "indicconformer"
    assert fields["LLM_PROVIDER"].default == "openai"
    assert fields["TTS_PROVIDER"].default == "piper"
    assert fields["LLM_FALLBACK_PROVIDER"].default == ""
    assert fields["STT_FALLBACK_TO_SARVAM"].default is False


def test_default_build_never_loads_the_sarvam_plugin():
    import subprocess
    import sys

    env = {k: v for k, v in __import__("os").environ.items() if not k.endswith("_PROVIDER")}
    code = ("import sys, agent.worker as w; "
            "assert 'livekit.plugins.sarvam' not in sys.modules; "
            "assert w.settings.STT_PROVIDER != 'sarvam' and w.settings.TTS_PROVIDER == 'piper'; "
            "print('clean')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert "clean" in out.stdout, out.stderr[-500:]


def test_worker_builds_openai_speech_with_its_own_key(monkeypatch):
    from agent import worker
    from livekit.plugins import openai as lk_openai

    monkeypatch.setattr(worker.settings, "TTS_PROVIDER", "openai")
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-test")
    built = worker.build_tts()
    assert isinstance(built, lk_openai.TTS) and built.sample_rate == 24000


async def test_openai_speech_client_request_and_failure(monkeypatch):
    from speech.bulbul_tts import BulbulTTSError
    from speech.openai_tts import OpenAITTSClient, create_tts_client

    _set(monkeypatch, OPENAI_API_KEY="sk-test", OPENAI_TTS_MODEL="gpt-4o-mini-tts", OPENAI_TTS_VOICE="marin", TTS_PROVIDER="openai")
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, content=b"\0\0" * 10)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAITTSClient(http)
        assert await client.synthesize_pcm("Nexus CRM Pro") == b"\0\0" * 10
        assert (await client.synthesize_wav("Ji"))[:4] == b"RIFF"
    assert seen["url"].endswith("/audio/speech") and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["response_format"] == "pcm" and seen["body"]["voice"] == "marin"
    assert seen["body"]["input"] == "Ji" and "instructions" in seen["body"]
    assert isinstance(create_tts_client(), OpenAITTSClient)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, text="slow down"))) as http:
        with pytest.raises(BulbulTTSError, match="429"):
            await OpenAITTSClient(http).synthesize_pcm("Ji")


def test_summaries_use_the_selected_llm_provider(monkeypatch):
    _set(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test", OPENAI_MODEL="gpt-4o-mini")
    assert LLMClient.offline().endpoints[0].provider == "openai"


def test_stored_bulbul_voices_are_ignored_on_openai(monkeypatch):
    _set(monkeypatch, TTS_PROVIDER="openai", OPENAI_TTS_VOICE="marin", OPENAI_TTS_SAMPLE_RATE=24000)
    s = get_settings()
    assert s.tts_voice == "marin" and s.tts_sample_rate == 24000 and s.tts_model == s.OPENAI_TTS_MODEL


def test_unknown_providers_are_rejected():
    with pytest.raises(ValidationError):
        Settings(STT_PROVIDER="nope", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(LLM_PROVIDER="nope", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(TTS_PROVIDER="svara", _env_file=None)


def _set(monkeypatch, **values):
    s = get_settings()
    for key, value in values.items():
        monkeypatch.setattr(s, key, value)
    return s


def test_worker_picks_the_stt_from_settings(monkeypatch):
    from agent import worker

    monkeypatch.setattr(worker.settings, "STT_PROVIDER", "whisper_hinglish")
    built = worker.build_stt()
    assert isinstance(built, LocalSTT) and built.engine == "whisper_hinglish"
    assert built.capabilities.manual_flush is True and built.capabilities.streaming is True


# ------------------------------------------------------------------------------
# LLM providers
# ------------------------------------------------------------------------------
def test_sarvam_requests_are_unchanged(monkeypatch):
    s = _set(monkeypatch, LLM_PROVIDER="sarvam", LLM_FALLBACK_PROVIDER="", REALTIME_LLM_FALLBACK_BASE_URL="",
             REALTIME_LLM_EXTRA_BODY='{"reasoning_effort": null}', REALTIME_LLM_BASE_URL="https://sarvam.test/v1",
             REALTIME_LLM_API_KEY="k", REALTIME_LLM_MODEL="sarvam-105b-conversations")
    ep = realtime_endpoints()[0]
    assert (ep.provider, ep.base_url, ep.model) == ("sarvam", s.REALTIME_LLM_BASE_URL, s.REALTIME_LLM_MODEL)
    client = LLMClient([ep])
    body = client._body(ep, [], None, 0.3, 160, True, None)
    assert body["max_tokens"] == 160 and body["temperature"] == 0.3 and body["reasoning_effort"] is None
    assert "max_completion_tokens" not in body


def test_openai_uses_its_own_key_url_and_token_field(monkeypatch):
    _set(monkeypatch, LLM_PROVIDER="openai", LLM_FALLBACK_PROVIDER="", OPENAI_API_KEY="sk-test", OPENAI_MODEL="gpt-4o-mini",
         REALTIME_LLM_FALLBACK_BASE_URL="")
    ep = realtime_endpoints()[0]
    assert ep.base_url == "https://api.openai.com/v1" and ep.api_key == "sk-test"
    body = LLMClient([ep])._body(ep, [], None, 0.3, 160, True, None)
    assert body["max_completion_tokens"] == 160 and "max_tokens" not in body
    assert body["temperature"] == 0.3 and "reasoning_effort" not in body


def test_openai_key_is_not_sent_in_the_sarvam_header(monkeypatch):
    from llm.client import _headers

    assert "api-subscription-key" not in _headers(LLMEndpoint("p", "u", "k", "m", {}, "openai"))
    assert _headers(LLMEndpoint("p", "u", "k", "m", {}, "sarvam"))["api-subscription-key"] == "k"


def test_reasoning_models_get_effort_and_no_temperature(monkeypatch):
    _set(monkeypatch, LLM_REASONING_EFFORT="")
    ep = LLMEndpoint("p", "u", "k", "gpt-5-mini", {}, "openai")
    body = LLMClient([ep])._body(ep, [], None, 0.3, 160, True, None)
    assert body["reasoning_effort"] == "minimal" and "temperature" not in body
    assert body["max_completion_tokens"] > 160  # hidden reasoning must not use up the whole reply
    assert is_reasoning_model("o3-mini") and not is_reasoning_model("gpt-4o-mini")
    assert default_reasoning_effort("gpt-5.5") == "none" and default_reasoning_effort("o3") == "low"
    _set(monkeypatch, LLM_REASONING_EFFORT="low")
    assert LLMClient([ep])._body(ep, [], None, 0.3, 160, True, None)["reasoning_effort"] == "low"


def test_gemini_uses_its_own_key_and_google_address(monkeypatch):
    _set(monkeypatch, LLM_REASONING_EFFORT="none", GEMINI_REASONING_EFFORT="minimal", GEMINI_API_KEY="g-key",
         GEMINI_MODEL="gemini-3.5-flash-lite", GEMINI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai")
    ep = build_endpoint("gemini", "primary")
    assert (ep.provider, ep.model, ep.api_key) == ("gemini", "gemini-3.5-flash-lite", "g-key")
    assert "generativelanguage.googleapis.com" in ep.base_url
    body = LLMClient([ep])._body(ep, [], None, 0.3, 160, True, None)
    assert body["max_tokens"] == 160 and body["reasoning_effort"] == "minimal"  # OpenAI's "none" doesn't leak to Gemini
    _set(monkeypatch, GEMINI_REASONING_EFFORT="")
    assert "reasoning_effort" not in LLMClient([ep])._body(ep, [], None, 0.3, 160, True, None)
    _set(monkeypatch, GEMINI_API_KEY="")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_endpoint("gemini", "primary")
    assert Settings(LLM_PROVIDER="gemini", _env_file=None).LLM_PROVIDER == "gemini"


def test_selfhosted_is_the_same_client_with_another_url(monkeypatch):
    _set(monkeypatch, LLM_REASONING_EFFORT="", SELFHOSTED_LLM_API_KEY="", SELFHOSTED_LLM_BASE_URL="http://gpu:8000/v1", SELFHOSTED_LLM_MODEL="sarvam-30b")
    ep = build_endpoint("selfhosted", "primary")
    assert ep.base_url == "http://gpu:8000/v1" and ep.api_key == "none"
    body = LLMClient([ep])._body(ep, [], None, 0.3, 160, True, None)
    assert body["max_tokens"] == 160 and "reasoning_effort" not in body


def test_missing_provider_settings_give_a_clear_error(monkeypatch):
    _set(monkeypatch, OPENAI_API_KEY="", SELFHOSTED_LLM_BASE_URL="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_endpoint("openai", "primary")
    with pytest.raises(ValueError, match="SELFHOSTED"):
        build_endpoint("selfhosted", "primary")


async def test_failover_moves_to_the_fallback_provider(monkeypatch):
    _set(monkeypatch, LLM_PROVIDER="openai", LLM_FALLBACK_PROVIDER="sarvam", OPENAI_API_KEY="sk-test", OPENAI_MODEL="gpt-4o-mini",
         REALTIME_LLM_BASE_URL="https://sarvam.test/v1", REALTIME_LLM_API_KEY="k", REALTIME_LLM_MODEL="sarvam-model")
    endpoints = realtime_endpoints()
    assert [e.provider for e in endpoints] == ["openai", "sarvam"]
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "openai" in str(request.url):
            return httpx.Response(500, json={"error": "down"})
        chunk = json.dumps({"choices": [{"delta": {"content": "Ji. "}}]})
        return httpx.Response(200, text=f"data: {chunk}\n\ndata: [DONE]\n\n")

    from llm import client as llm_client

    llm_client._BREAKERS.clear()
    client = LLMClient(endpoints, ttft_timeout=1.0, hard_timeout=5.0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    events = [e async for e in client.stream_chat([{"role": "user", "content": "hi"}])]
    assert "".join(e.text for e in events if e.type == "text") == "Ji. "
    assert "openai" in seen[0] and "sarvam.test" in seen[1]
    llm_client._BREAKERS.clear()


# ------------------------------------------------------------------------------
# STT adapter
# ------------------------------------------------------------------------------
def _frames(seconds=0.2, rate=8000):
    pcm = (np.sin(np.linspace(0, 300, int(rate * seconds))) * 8000).astype(np.int16).tobytes()
    return rtc.AudioFrame(data=pcm, sample_rate=rate, num_channels=1, samples_per_channel=len(pcm) // 2)


async def _run_stream(stt):
    stream = stt.stream()
    stream.push_frame(_frames())
    stream.flush()
    stream.end_input()
    return [event async for event in stream]


def _finals(events):
    return [e.alternatives[0].text for e in events if e.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT]


async def test_adapter_emits_a_final_transcript_on_flush():
    calls = []

    async def decoder(pcm, rate):
        calls.append((len(pcm), rate))
        return "haan ji"

    async def fallback(pcm, rate):
        raise AssertionError("fallback must not run")

    events = await _run_stream(LocalSTT("whisper_hinglish", decoder=decoder, fallback=fallback))
    assert _finals(events) == ["haan ji"]
    assert calls[0][1] == 8000 and calls[0][0] > 0
    assert any(e.type == lk_stt.SpeechEventType.RECOGNITION_USAGE for e in events)
    assert events[-1].type == lk_stt.SpeechEventType.END_OF_SPEECH


async def test_adapter_falls_back_to_sarvam_with_the_same_audio(monkeypatch):
    _set(monkeypatch, STT_FALLBACK_TO_SARVAM=True, SARVAM_API_KEY="real-key")
    received = {}

    async def broken(pcm, rate):
        received["local"] = pcm
        raise ServerDecodeError("busy")

    async def fallback(pcm, rate):
        received["sarvam"] = pcm
        return "nahi chahiye"

    events = await _run_stream(LocalSTT("whisper_hinglish", decoder=broken, fallback=fallback))
    assert _finals(events) == ["nahi chahiye"] and received["local"] == received["sarvam"]


async def test_adapter_without_a_sarvam_key_gives_no_transcript(monkeypatch):
    _set(monkeypatch, STT_FALLBACK_TO_SARVAM=True, SARVAM_API_KEY="mock-key")

    async def broken(pcm, rate):
        raise ServerDecodeError("down")

    async def fallback(pcm, rate):
        raise AssertionError("no key: fallback must not run")

    assert _finals(await _run_stream(LocalSTT("whisper_hinglish", decoder=broken, fallback=fallback))) == []


async def test_default_has_no_outside_fallback(monkeypatch):
    _set(monkeypatch, STT_FALLBACK_TO_SARVAM=False, SARVAM_API_KEY="real-key")

    async def broken(pcm, rate):
        raise ServerDecodeError("down")

    async def fallback(pcm, rate):
        raise AssertionError("Sarvam must not be called")

    assert _finals(await _run_stream(LocalSTT("indicconformer", decoder=broken, fallback=fallback))) == []


async def test_a_real_timeout_or_refused_connection_becomes_a_decode_error(monkeypatch):
    from speech.local_stt import decode_on_server

    _set(monkeypatch, STT_SERVER_URL="ws://127.0.0.1:1/v1/stt")
    with pytest.raises(ServerDecodeError):
        await decode_on_server("whisper_hinglish", b"\0\0", 8000)


# ------------------------------------------------------------------------------
# STT server
# ------------------------------------------------------------------------------
class FakeEngine(Engine):
    def __init__(self, name):
        self.name = name
        self.closed = False
        self.loads = 0

    def load(self):
        self.loads += 1

    def transcribe(self, audio):
        assert audio.dtype == np.float32
        return f"{self.name}:{audio.size > 0}"

    def close(self):
        self.closed = True


def _speech(rate=8000, seconds=1.0):
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    audio = np.concatenate([np.zeros(rate // 2), 0.3 * np.sin(2 * np.pi * 220 * t), np.zeros(rate // 2)]).astype(np.float32)
    return float_to_pcm16(audio)


@pytest.fixture
def fake_host(monkeypatch):
    built = []

    def factory(name):
        engine = FakeEngine(name)
        built.append(engine)
        return engine

    host = server.EngineHost(factory)
    monkeypatch.setattr(server, "host", host)
    _set(monkeypatch, SPEECH_SERVER_TOKEN="")  # tests don't depend on the token in a local .env
    return built


def _decode(client, engine, pcm):
    with client.websocket_connect("/v1/stt") as ws:
        ws.send_json({"type": "config", "engine": engine, "sample_rate": 8000})
        ws.send_bytes(pcm)
        ws.send_json({"type": "flush"})
        return ws.receive_json()


def test_server_decodes_and_keeps_one_model_loaded(fake_host):
    client = TestClient(server.app)
    assert client.get("/health").json()["engine"] is None  # nothing loads until it is needed
    reply = _decode(client, "whisper_hinglish", _speech())
    assert reply["type"] == "final" and reply["text"] == "whisper_hinglish:True"
    assert 0.9 < reply["audio_seconds"] < 1.5  # the silence was trimmed
    _decode(client, "whisper_hinglish", _speech())
    assert len(fake_host) == 1
    _decode(client, "indicconformer", _speech())
    assert len(fake_host) == 2 and fake_host[0].closed is True
    assert client.get("/health").json()["engine"] == "indicconformer"


def test_server_returns_nothing_for_silence_without_decoding(fake_host):
    reply = _decode(TestClient(server.app), "whisper_hinglish", b"\0\0" * 8000)
    assert reply["text"] == "" and fake_host == []


def test_server_reports_backlog_and_engine_errors(fake_host, monkeypatch):
    client = TestClient(server.app)
    server.host.waiting = 99
    assert _decode(client, "whisper_hinglish", _speech())["type"] == "busy"
    server.host.waiting = 0

    def unavailable(name):
        raise EngineUnavailable("needs a token")

    server.host.factory = unavailable
    reply = _decode(client, "indicconformer", _speech())
    assert reply == {"type": "error", "message": "needs a token"}


def test_resampling_and_silence_trim():
    audio = np.zeros(8000, dtype=np.float32)
    assert resample(audio, 8000).size == pytest.approx(16000, abs=10)
    assert trim_silence(audio, 16000).size == 0


def test_gated_engine_says_what_to_do_without_a_token(monkeypatch, tmp_path):
    _set(monkeypatch, HF_TOKEN="", STT_INDICCONFORMER_PATH=str(tmp_path))
    with pytest.raises(EngineUnavailable, match="HF_TOKEN"):
        IndicConformerEngine().load()
    with pytest.raises(EngineUnavailable, match="Unknown"):
        create_engine("nope")


def test_mel_features_have_the_shape_the_model_expects():
    from speech.stt_server.mel import N_MELS, log_mel, mel_filterbank

    assert mel_filterbank().shape == (N_MELS, 257)
    audio = np.sin(np.linspace(0, 2000, 16000)).astype(np.float32)
    features, frames = log_mel(audio)
    assert features.shape == (1, N_MELS, 101) and frames == 101 and features.dtype == np.float32
    assert abs(float(features[0, :, :frames].mean())) < 1e-3  # each mel band is normalized


def test_ctc_output_collapses_repeats_and_blanks():
    class FakeSession:
        def __init__(self, result):
            self.result = result

        def run(self, names, inputs):
            return self.result

    engine = IndicConformerEngine()
    picks = [0, 0, 256, 1, 1, 256, 2]  # two tokens with a blank between, repeats collapsed
    logprobs = np.full((1, len(picks), 257), -9.0, dtype=np.float32)
    logprobs[0, np.arange(len(picks)), picks] = 0.0
    engine.encoder = FakeSession([np.zeros((1, 1, 1)), np.array([1])])
    engine.decoder = FakeSession([logprobs])
    engine.vocab = ["\u2581हाँ", "\u2581जी", "।"] + ["x"] * 254
    engine.mask = list(range(257))
    assert engine.transcribe_devanagari(np.zeros(16000, dtype=np.float32)) == "हाँ जी।"
    assert engine.transcribe(np.zeros(16000, dtype=np.float32)) == "haan ji."


# ------------------------------------------------------------------------------
# Transliteration
# ------------------------------------------------------------------------------
def test_pluggable_transliterator_and_roman_passthrough():
    custom = HinglishTransliterator(fallback=lambda word: "X")
    assert custom.to_roman("कखग") == "X"
    assert custom.to_roman("call mat karna") == "call mat karna"
    assert custom.to_roman("कॉल ५ बजे") == "call 5 baje"
    assert custom.to_roman("") == ""


def test_rule_based_step_drops_hindi_schwa():
    pytest.importorskip("indic_transliteration")
    from speech.transliterate import _rule_based

    assert _rule_based("करते") == "karte"
    assert _rule_based("मतलब") == "matlab"
    assert _rule_based("समझ") == "samajh"
    assert _rule_based("कितना") == "kitna"
    assert _rule_based("शाम") == "shaam"
    assert _rule_based("न") == "na"


@pytest.mark.parametrize("devanagari,intent", [
    ("मुझे दोबारा कॉल मत करना", "opt_out"),
    ("कॉल मत करो", "opt_out"),
    ("फ़ोन मत करो", "opt_out"),
    ("मेरा नंबर हटा दो", "opt_out"),
    ("लिस्ट से हटा दो", "opt_out"),
    ("कॉल करना बंद करो", "opt_out"),
    ("डीएनडी में डाल दो", "opt_out"),
    ("बाद में कॉल करना", "call_later"),
    ("अभी बिज़ी हूं", "call_later"),
    ("कल कॉल करना", "call_later"),
    ("गलत नंबर है", "wrong_person"),
    ("मैनेजर से बात", "transfer_human"),
    ("शिकायत दर्ज करनी है", "complaint"),
])
def test_devanagari_phrases_reach_the_fast_path_guard(devanagari, intent):
    """A missed opt-out here is a legal problem, so these must keep passing."""
    result = FastPathGuard().evaluate(apply_lexicon(to_roman(devanagari)))
    assert result.intent == intent, to_roman(devanagari)


def test_time_words_reach_the_callback_parser():
    from agent.callback_time import parse_callback_time

    assert parse_callback_time(to_roman("कल शाम पांच बजे")).hour == 17


# ------------------------------------------------------------------------------
# Bake-off scoring
# ------------------------------------------------------------------------------
def test_bakeoff_scoring_treats_all_engines_the_same():
    pytest.importorskip("jiwer")
    from scripts.stt_bakeoff import is_short_answer, make_row, normalize_text, score, summarize

    assert normalize_text("Haan, ji!") == normalize_text("हाँ जी")
    assert score("haan ji", "हां जी")["wer"] == 0.0
    assert score("kal shaam 5 baje", "kal sham 5 baje")["wer"] == 0.0
    assert score("kal shaam 5 baje", "kal subah 5 baje")["wer"] == 0.25
    assert score("haan", "")["wer"] == 1.0
    assert is_short_answer("kal shaam 5 baje") and not is_short_answer("abhi hum manually excel sheet mein leads update karte hain")

    rows = [make_row("a.wav", "x", "haan ji", "haan ji", 0.5, 1.0), make_row("b.wav", "x", "nahi", "haan", 0.5, 1.0),
            make_row("a.wav", "y", "haan ji", None, 2.0, 1.0)]
    x, y = summarize(rows)
    assert x["wer"] == 0.5 and x["short_exact"] == 1 and x["short_answers"] == 2 and x["rtf"] == 0.5
    assert y["wer"] is None and y["rtf"] == 2.0


def test_bakeoff_csv(tmp_path):
    pytest.importorskip("jiwer")
    from scripts.stt_bakeoff import load_refs, make_row, write_csv

    (tmp_path / "a.txt").write_text("haan ji", encoding="utf-8")
    assert load_refs(str(tmp_path)) == {"a": "haan ji"}
    out = tmp_path / "out.csv"
    write_csv(str(out), [make_row("a.wav", "x", "haan ji", "haan ji", 0.5, 1.0)])
    text = out.read_text(encoding="utf-8-sig")
    assert "real_time_factor" in text and "a.wav,x,haan ji,haan ji,0.0,0.0,0.5,1.0,0.5" in text


# ------------------------------------------------------------------------------
# Offline TTS renderer
# ------------------------------------------------------------------------------
def test_tts_token_mapping_follows_the_snac_layout():
    from speech.local_tts_render import AUDIO_BASE, CODEBOOK, split_frames, svara_prompt, tokens_to_codes

    frame = [AUDIO_BASE + i * CODEBOOK + i + 1 for i in range(7)]
    assert tokens_to_codes([5, *frame, 128258]) == [1, 2, 3, 4, 5, 6, 7]  # non-audio tokens are dropped
    assert tokens_to_codes(frame + frame[:3]) == [1, 2, 3, 4, 5, 6, 7]  # an incomplete frame is dropped
    assert split_frames([1, 2, 3, 4, 5, 6, 7]) == [[1], [2, 5], [3, 4, 6, 7]]
    prompt = svara_prompt([9])
    assert prompt[0] == 128000 and prompt[3] == 9 and prompt[-1] == 128257


def test_hinglish_becomes_devanagari_for_the_local_voice():
    from speech.devanagari import to_devanagari

    assert to_devanagari("Hello Rajesh ji, aap kaise hain?") == "हेलो राजेश जी, आप कैसे हैं?"
    assert to_devanagari("C R M demo") == "सी आर एम डेमो"
    assert to_devanagari("नेक्सस ka price") == "नेक्सस का प्राइस"
    assert to_devanagari("teen") == "तीन"  # not on the list, so the spelling rules handle it
    assert to_devanagari("ninyaanve") == "निन्यान्वे"


def test_every_scripted_line_becomes_pure_devanagari():
    import re

    from speech.devanagari import to_devanagari
    from speech.normalizer import HinglishNormalizer
    from speech.phrase_cache import PreRenderedPhraseCache as P

    cache = P(voice="x", company="NavyaNexus", persona="Priya", complaint_contact="support")
    for phrase_id in cache.CANONICAL_TEXTS:
        assert not re.search("[A-Za-z]", to_devanagari(HinglishNormalizer.normalize(cache.get_text(phrase_id)))), phrase_id


def test_local_voice_is_selected_and_costs_nothing(monkeypatch):
    from observability.metrics import CostCalculator
    from speech.openai_tts import create_tts_client
    from speech.piper_tts import PiperTTSClient

    _set(monkeypatch, TTS_PROVIDER="piper")
    client = create_tts_client()
    assert isinstance(client, PiperTTSClient) and client.configured and client.sample_rate == 22050
    assert CostCalculator.tts_rate_per_10k() == 0.0
