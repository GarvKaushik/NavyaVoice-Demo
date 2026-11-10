"""Latency benchmark of every step in the voice pipeline, against the live vendors.

    python -m scripts.benchmark            # all steps, default repeats
    python -m scripts.benchmark -n 10      # more samples for steadier p50/p95

Run it from the region you deploy to (Mumbai VM) for numbers that match production.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Dict, List

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from livekit import rtc
from livekit.agents import stt as lk_stt
from livekit.plugins import sarvam

from agent.knowledge import load_knowledge
from agent.observer import CallState, OBSERVER_PROMPT
from agent.prompts import build_messages
from agent.stages import SalesStage, StageManager
from agent.tools import TOOL_SCHEMAS
from config.settings import get_settings
from guardrails.output_guard import OutputGuard
from guardrails.fast_path_guard import FastPathGuard
from llm.client import LLMClient
from speech.bulbul_tts import BulbulTTSClient, shared_http
from speech.normalizer import HinglishNormalizer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

s = get_settings()
UTTERANCES = [
    "Ji haan, boliye.",
    "Iska price kya hai?",
    "Abhi hum manually Excel sheet mein update karte hain aur time lag jata hai.",
    "Thoda mehenga lag raha hai, koi offer hai kya?",
]
AGENT_SENTENCES = [
    "Ji bilkul.",
    "Nexus CRM Pro ka price ₹1,499 per month hai.",
    "Ismein automatic calling aur seedhi CRM entry milti hai, jisse Excel ka kaam khatam ho jata hai.",
]


def stats(xs: List[float]) -> Dict[str, float]:
    xs = sorted(xs)
    p95 = xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))]
    return {"n": len(xs), "p50": round(statistics.median(xs)), "p95": round(p95), "min": round(xs[0]), "max": round(xs[-1])}


def show(name: str, xs: List[float], note: str = "") -> Dict[str, float]:
    st = stats(xs)
    print(f"  {name:<44} p50 {st['p50']:>5} ms   p95 {st['p95']:>5} ms   (n={st['n']}) {note}")
    return st


async def bench_stt_rest(n: int, clips: List[bytes]) -> List[float]:
    out = []
    for i in range(n):
        wav = clips[i % len(clips)]
        t0 = time.monotonic()
        r = await shared_http().post(s.SARVAM_STT_URL, headers={"api-subscription-key": s.SARVAM_API_KEY},
                                     data={"model": s.SARVAM_STT_MODEL, "mode": s.SARVAM_STT_MODE, "language_code": "hi-IN"},
                                     files={"file": ("a.wav", wav, "audio/wav")})
        r.raise_for_status()
        out.append((time.monotonic() - t0) * 1000)
    return out


async def bench_stt_stream(pcm_clips: List[bytes], session: aiohttp.ClientSession) -> List[float]:
    """Streams audio in real time (20 ms frames) and measures end-of-speech -> final transcript."""
    stt = sarvam.STT(language="hi-IN", model=s.SARVAM_STT_MODEL, mode=s.SARVAM_STT_MODE, api_key=s.SARVAM_API_KEY,
                     sample_rate=8000, http_session=session)
    out = []
    for pcm in pcm_clips:
        stream = stt.stream()
        speech_end = None

        async def feed():
            nonlocal speech_end
            step = 160 * 2  # 20 ms at 8 kHz
            padded = pcm + b"\x00" * (8000 * 2 * 2)  # 2 s of silence after speech
            for i in range(0, len(padded), step):
                chunk = padded[i:i + step].ljust(step, b"\x00")
                stream.push_frame(rtc.AudioFrame(data=chunk, sample_rate=8000, num_channels=1, samples_per_channel=160))
                if i >= len(pcm) and speech_end is None:
                    speech_end = time.monotonic()
                await asyncio.sleep(0.02)
            stream.end_input()

        feeder = asyncio.create_task(feed())
        try:
            async for ev in stream:
                if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives and ev.alternatives[0].text.strip():
                    if speech_end:
                        out.append((time.monotonic() - speech_end) * 1000)
                    break
        finally:
            feeder.cancel()
            await stream.aclose()
    return out


async def bench_llm(n: int) -> Dict[str, List[float]]:
    llm = LLMClient.realtime()
    k = await load_knowledge("CAMP-REENGAGE-Q4")
    stage = StageManager(SalesStage.PITCH)
    state = CallState("L", "+91", "C", identified_needs=["leads get missed"])
    history = [{"role": "user", "content": "Haan boliye"},
               {"role": "assistant", "content": "Aap abhi leads ko kaise call karte hain?"}]
    res = {"ttft_plain": [], "full_plain": [], "tool_round": [], "ttft_after_tool": [], "first_sentence_tool": [], "observer": []}

    for i in range(n):
        msgs = build_messages(k, stage, state, "Rajesh", history + [{"role": "user", "content": "Hum Excel use karte hain, time lagta hai."}])
        t0, first = time.monotonic(), None
        async for ev in llm.stream_chat(msgs, tools=TOOL_SCHEMAS):
            if ev.type in ("text", "tool_calls") and first is None:
                first = time.monotonic()
        res["ttft_plain"].append((first - t0) * 1000)
        res["full_plain"].append((time.monotonic() - t0) * 1000)

        # A turn that needs a tool: price question -> get_price -> spoken answer
        msgs = build_messages(k, stage, state, "Rajesh", history + [{"role": "user", "content": "Iska price kya hai?"}])
        t0 = time.monotonic()
        calls = []
        async for ev in llm.stream_chat(msgs, tools=TOOL_SCHEMAS):
            if ev.type == "tool_calls":
                calls = ev.tool_calls
        t_tool = time.monotonic()
        res["tool_round"].append((t_tool - t0) * 1000)
        if calls:
            msgs.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls]})
            for c in calls:
                msgs.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(
                    {"ok": True, "price_inr": 1499, "approved_phrasing": "Nexus CRM Pro ka price ₹1,499 per month hai."})})
            t1, first, buf = time.monotonic(), None, ""
            async for ev in llm.stream_chat(msgs, tools=TOOL_SCHEMAS):
                if ev.type == "text":
                    if first is None:
                        first = time.monotonic()
                        res["ttft_after_tool"].append((first - t1) * 1000)
                    buf += ev.text
                    if any(p in buf for p in ".?!"):
                        res["first_sentence_tool"].append((time.monotonic() - t0) * 1000)
                        break

        t0 = time.monotonic()
        await llm.complete([{"role": "system", "content": OBSERVER_PROMPT},
                            {"role": "user", "content": json.dumps({"agent_said": "Price ₹1,499 hai.", "customer_said": "Thoda mehenga hai"})}],
                           max_tokens=200, json_mode=True, purpose="observer")
        res["observer"].append((time.monotonic() - t0) * 1000)
    await llm.aclose()
    return res


async def bench_tts_rest(n: int) -> List[float]:
    tts = BulbulTTSClient()
    out = []
    for i in range(n):
        t0 = time.monotonic()
        await tts.synthesize_wav(AGENT_SENTENCES[i % len(AGENT_SENTENCES)])
        out.append((time.monotonic() - t0) * 1000)
    return out


async def bench_tts_stream(n: int, session: aiohttp.ClientSession) -> List[float]:
    tts = sarvam.TTS(target_language_code="hi-IN", model=s.BULBUL_TTS_MODEL, speaker=s.BULBUL_TTS_VOICE_ID,
                     speech_sample_rate=8000, api_key=s.SARVAM_API_KEY, output_audio_codec="linear16",
                     http_session=session)
    out = []
    for i in range(n):
        text = HinglishNormalizer.normalize(AGENT_SENTENCES[i % len(AGENT_SENTENCES)])
        stream = tts.stream()
        t0 = time.monotonic()
        stream.push_text(text)
        stream.end_input()
        async for _ev in stream:
            out.append((time.monotonic() - t0) * 1000)
            break
        await stream.aclose()
    await tts.aclose()
    return out


def bench_local(n: int = 2000) -> Dict[str, float]:
    guard, fast = OutputGuard(), FastPathGuard()
    t0 = time.perf_counter()
    for _ in range(n):
        fast.evaluate("Thoda mehenga lag raha hai, koi offer hai kya?")
    fp = (time.perf_counter() - t0) / n * 1000
    t0 = time.perf_counter()
    for _ in range(n):
        guard.verify_sentence("Nexus CRM Pro ka price ₹1,499 per month hai.", {1499.0}, set())
    og = (time.perf_counter() - t0) / n * 1000
    t0 = time.perf_counter()
    for _ in range(n):
        HinglishNormalizer.normalize("Nexus CRM Pro ka price ₹1,499 per month hai, 20% off ke saath.")
    nm = (time.perf_counter() - t0) / n * 1000
    return {"fast_path_ms": fp, "output_guard_ms": og, "normalizer_ms": nm}


async def main(n: int) -> None:
    print(f"LLM {s.REALTIME_LLM_MODEL} | STT {s.SARVAM_STT_MODEL}/{s.SARVAM_STT_MODE} | TTS {s.BULBUL_TTS_MODEL}/{s.BULBUL_TTS_VOICE_ID}\n")
    tts8 = BulbulTTSClient(sample_rate=8000)
    wav_clips = [await tts8.synthesize_wav(u, normalize=False) for u in UTTERANCES]
    pcm_clips = [await tts8.synthesize_pcm(u, normalize=False) for u in UTTERANCES]
    results = {}
    async with aiohttp.ClientSession() as session:
        print("Speech-to-text")
        results["stt_stream"] = show("Streaming STT: end of speech -> final text", await bench_stt_stream(pcm_clips, session), "(phone calls)")
        results["stt_rest"] = show("REST STT: whole utterance", await bench_stt_rest(n, wav_clips), "(browser console)")
        print("LLM")
        llm = await bench_llm(n)
        results["llm_ttft"] = show("First token, normal turn", llm["ttft_plain"])
        results["llm_full"] = show("Complete reply, normal turn", llm["full_plain"])
        results["llm_tool_round"] = show("Tool turn: deciding to call get_price", llm["tool_round"])
        results["llm_ttft_after_tool"] = show("Tool turn: first token after tool result", llm["ttft_after_tool"])
        results["llm_first_sentence_tool"] = show("Tool turn: first full sentence", llm["first_sentence_tool"])
        results["observer"] = show("Observer (async, off the reply path)", llm["observer"])
        print("Text-to-speech")
        results["tts_stream"] = show("Streaming TTS: first audio (WebSocket)", await bench_tts_stream(n, session), "(phone calls)")
        results["tts_rest"] = show("REST TTS: whole sentence", await bench_tts_rest(n), "(browser console)")
    local = bench_local()
    print("Local processing (per turn)")
    for k, v in local.items():
        print(f"  {k:<44} {v:.3f} ms")
    results["local"] = local
    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("\nSaved benchmark_results.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=6)
    asyncio.run(main(ap.parse_args().n))
