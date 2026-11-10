"""Cost of a realistic sales call, from real LLM token usage and real TTS character counts.

    python -m scripts.cost_report

Runs a full scripted conversation through the live engine (reply LLM + observer),
then prices it with the rates in config/settings.py.
"""

import asyncio
import sys
import uuid
from datetime import date

from agent.dialogue import DialogueEngine
from agent.knowledge import load_knowledge
from agent.tools import CallIdentity
from config.settings import get_settings
from llm.client import LLMClient
from observability.metrics import CostCalculator
from speech.normalizer import HinglishNormalizer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CUSTOMER = [
    "Ji haan.",
    "Haan boliye, do minute hain.",
    "Abhi hum manually Excel sheet mein leads update karte hain aur calls mein bahut time lagta hai.",
    "Haan, sahi samjha aapne.",
    "Achha, iska price kya hai?",
    "Thoda mehenga lag raha hai.",
    "Theek hai, WhatsApp par link bhej dijiye.",
    "Nahi, bas itna hi. Dhanyawaad.",
]

TTS_CHARS_PER_SEC = 18.0    # Bulbul v4 Flash at pace 1.0 (measured from the pre-rendered phrases)
CALLER_WORDS_PER_SEC = 2.5  # conversational Hinglish
TURN_GAP_SEC = 1.3          # pause + response latency per exchange


async def main() -> None:
    s = get_settings()
    llm = LLMClient.realtime()
    knowledge = await load_knowledge("CAMP-REENGAGE-Q4")
    identity = CallIdentity(f"cost_{uuid.uuid4().hex[:6]}", "LEAD-TEST-001", "CAMP-REENGAGE-Q4", "+919876543210", "Rajesh")
    engine = DialogueEngine(identity, knowledge, llm, observer_llm=llm)

    tts_live, tts_cached, agent_secs = 0, 0, 0.0
    for u in engine.opening():
        n = len(HinglishNormalizer.normalize(u.text))
        agent_secs += n / TTS_CHARS_PER_SEC
        tts_cached, tts_live = (tts_cached + n, tts_live) if u.phrase_id else (tts_cached, tts_live + n)

    caller_secs, llm_calls = 0.0, 0
    for said in CUSTOMER:
        caller_secs += len(said.split()) / CALLER_WORDS_PER_SEC
        scripted = await engine.pre_turn(said)
        utterances = scripted.utterances if scripted else [u async for u in engine.stream_reply()]
        llm_calls += 0 if scripted else 1
        for u in utterances:
            n = len(HinglishNormalizer.normalize(u.text))
            agent_secs += n / TTS_CHARS_PER_SEC
            tts_cached, tts_live = (tts_cached + n, tts_live) if u.phrase_id else (tts_cached, tts_live + n)
        print(f"  you: {said}\n  agent: {' '.join(u.text for u in utterances)}")
        if engine.pending_action:
            break
    await engine.drain_observer(timeout=10)

    reply, observer = engine.usage, engine.observer.usage
    duration = int(agent_secs + caller_secs + TURN_GAP_SEC * len(CUSTOMER) + 3)
    minutes = duration / 60
    total_in = reply.input_tokens + observer.input_tokens
    total_cached = reply.cached_tokens + observer.cached_tokens
    total_out = reply.output_tokens + observer.output_tokens

    print(f"\nCall: {duration}s ({minutes:.2f} min), {engine.turns} caller turns, outcome {engine.outcome}")
    print(f"Agent speech ~{agent_secs:.0f}s, caller speech ~{caller_secs:.0f}s")
    print(f"TTS chars: {tts_live} synthesized + {tts_cached} from cache ({tts_cached / max(tts_live + tts_cached, 1):.0%} cached)")
    print(f"LLM tokens: reply in {reply.input_tokens} (cached {reply.cached_tokens}) out {reply.output_tokens}; "
          f"observer in {observer.input_tokens} out {observer.output_tokens}")

    scenarios = {
        "STT streams whole call (now)": duration,
        "STT VAD-gated (plan)": caller_secs + 0.3 * engine.turns,
    }
    for label, on in [("promo TTS ₹10/10K (until 12 Nov)", date(2026, 11, 5)), ("standard TTS ₹20/10K", date(2026, 12, 1))]:
        for stt_label, stt_secs in scenarios.items():
            c = CostCalculator.compute_call_cost(duration_seconds=duration, stt_audio_seconds=stt_secs,
                                                 tts_character_count=tts_live, llm_input_tokens=total_in,
                                                 llm_cached_tokens=total_cached, llm_output_tokens=total_out, on=on)
            per_min = lambda x: x / minutes  # noqa: E731
            print(f"\n{label} | {stt_label}")
            for name, v in [("telephony", c.telephony_inr), ("stt", c.stt_inr), ("tts", c.tts_inr), ("llm", c.llm_inr),
                            ("orchestration", c.orchestration_inr), ("infra", c.infra_inr),
                            ("contingency", c.contingency_inr)]:
                print(f"  {name:<14} ₹{v:7.3f}/call   ₹{per_min(v):6.3f}/min")
            print(f"  {'TOTAL':<14} ₹{c.total_inr:7.3f}/call   ₹{c.per_connected_minute_inr:6.3f}/min")
    await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
