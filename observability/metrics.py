"""Observability Metrics & Unit Cost Accounting.

Tracks:
1. Voice-to-voice turn latencies (p50 target <= 1.1s, p95 <= 1.8s)
2. LLM Time-To-First-Token (target <= 400ms)
3. Output Guard replacements (< 1 per 50 calls)
4. Vendor errors, opt-outs, active calls
5. Cost ledger per connected minute (target <= ₹2.00, ceiling ₹2.50)
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from config.settings import get_settings

try:
    from prometheus_client import Counter, Histogram, Gauge

    LATENCY_VOICE_TO_VOICE = Histogram(
        "voice_to_voice_latency_seconds",
        "Caller end-of-speech to first agent audio",
        buckets=[0.5, 0.8, 1.1, 1.4, 1.8, 2.2, 3.0, 5.0],
    )
    LATENCY_LLM_TTFT = Histogram(
        "llm_time_to_first_token_seconds",
        "Time to first token from the LLM",
        ["endpoint", "purpose"],
        buckets=[0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1.2, 2.0, 4.0],
    )
    COUNTER_GUARD_REPLACEMENTS = Counter(
        "guard_replacements_total", "Sentences replaced by the Output Guard", ["rule"],
    )
    COUNTER_OPT_OUTS = Counter("opt_outs_total", "Opt-outs recorded")
    COUNTER_VENDOR_CALLS = Counter("vendor_calls_total", "Vendor calls", ["vendor", "result"])
    COUNTER_VENDOR_ERRORS = Counter("vendor_errors_total", "Vendor errors", ["vendor", "kind"])
    COUNTER_FAST_PATH = Counter("fast_path_hits_total", "Fast-path intercepts", ["intent"])
    GAUGE_ACTIVE_CALLS = Gauge("active_voice_calls", "Current concurrent connected voice calls")
except ImportError:  # pragma: no cover
    LATENCY_VOICE_TO_VOICE = LATENCY_LLM_TTFT = COUNTER_GUARD_REPLACEMENTS = None
    COUNTER_OPT_OUTS = COUNTER_VENDOR_CALLS = COUNTER_VENDOR_ERRORS = COUNTER_FAST_PATH = None
    GAUGE_ACTIVE_CALLS = None


def observe_llm_ttft(seconds: float, endpoint: str, purpose: str) -> None:
    if LATENCY_LLM_TTFT:
        LATENCY_LLM_TTFT.labels(endpoint=endpoint, purpose=purpose).observe(seconds)


def observe_voice_to_voice(seconds: float) -> None:
    if LATENCY_VOICE_TO_VOICE:
        LATENCY_VOICE_TO_VOICE.observe(seconds)


def record_vendor_call(vendor: str, ok: bool) -> None:
    if COUNTER_VENDOR_CALLS:
        COUNTER_VENDOR_CALLS.labels(vendor=vendor, result="ok" if ok else "error").inc()


def record_vendor_error(vendor: str, kind: str) -> None:
    if COUNTER_VENDOR_ERRORS:
        COUNTER_VENDOR_ERRORS.labels(vendor=vendor, kind=kind).inc()


def record_guard_intervention(rule_name: str) -> None:
    if COUNTER_GUARD_REPLACEMENTS:
        COUNTER_GUARD_REPLACEMENTS.labels(rule=rule_name).inc()


def record_opt_out() -> None:
    if COUNTER_OPT_OUTS:
        COUNTER_OPT_OUTS.inc()


def record_fast_path(intent: str) -> None:
    if COUNTER_FAST_PATH:
        COUNTER_FAST_PATH.labels(intent=intent).inc()


@dataclass
class CallCostBreakdown:
    """Itemized cost ledger for a call."""
    telephony_inr: float
    stt_inr: float
    tts_inr: float
    llm_inr: float
    orchestration_inr: float
    infra_inr: float
    contingency_inr: float
    total_inr: float
    duration_seconds: int
    billed_minutes: float
    per_connected_minute_inr: float


class CostCalculator:
    """Computes per-call cost from actual usage meters."""

    @classmethod
    def tts_rate_per_10k(cls, on: Optional[date] = None) -> float:
        s = get_settings()
        on = on or date.today()
        if on <= s.BULBUL_TTS_PROMO_UNTIL:
            return s.BULBUL_TTS_PROMO_RATE_PER_10K_CHARS_INR
        return s.BULBUL_TTS_RATE_PER_10K_CHARS_INR

    @classmethod
    def compute_call_cost(
        cls,
        duration_seconds: int,
        stt_audio_seconds: float,
        tts_character_count: int,
        llm_turn_count: int = 0,
        llm_input_tokens: Optional[int] = None,
        llm_cached_tokens: int = 0,
        llm_output_tokens: int = 0,
        on: Optional[date] = None,
    ) -> CallCostBreakdown:
        s = get_settings()
        minutes = max(duration_seconds, 0) / 60.0

        # Telephony: Plivo India bills in pulses (60 s unless per-second negotiated)
        pulse = max(s.PLIVO_BILLING_PULSE_SECONDS, 1)
        pulses = math.ceil(max(duration_seconds, 1) / pulse)
        billed_minutes = pulses * pulse / 60.0
        telephony = billed_minutes * s.PLIVO_RATE_PER_MINUTE_INR

        stt = (stt_audio_seconds / 3600.0) * s.SARVAM_STT_RATE_PER_HOUR_INR
        tts = (tts_character_count / 10000.0) * cls.tts_rate_per_10k(on)

        if llm_input_tokens is not None:
            uncached = max(llm_input_tokens - llm_cached_tokens, 0)
            llm = (uncached * s.LLM_INPUT_PRICE_PER_M_INR
                   + llm_cached_tokens * s.LLM_CACHED_INPUT_PRICE_PER_M_INR
                   + llm_output_tokens * s.LLM_OUTPUT_PRICE_PER_M_INR) / 1_000_000
        else:
            llm = minutes * 0.10  # plan estimate when token meters are unavailable

        orchestration = minutes * s.ORCHESTRATION_COST_PER_MIN_INR
        infra = minutes * s.INFRA_COST_PER_MIN_INR
        contingency = minutes * s.CONTINGENCY_COST_PER_MIN_INR
        total = telephony + stt + tts + llm + orchestration + infra + contingency

        return CallCostBreakdown(
            telephony_inr=round(telephony, 4),
            stt_inr=round(stt, 4),
            tts_inr=round(tts, 4),
            llm_inr=round(llm, 4),
            orchestration_inr=round(orchestration, 4),
            infra_inr=round(infra, 4),
            contingency_inr=round(contingency, 4),
            total_inr=round(total, 4),
            duration_seconds=duration_seconds,
            billed_minutes=billed_minutes,
            per_connected_minute_inr=round(total / minutes, 4) if minutes > 0 else 0.0,
        )


class VoiceAgentMetrics:
    """Backwards-compatible helper."""

    @staticmethod
    def record_turn_latency(total_seconds: float, ttft_seconds: float):
        observe_voice_to_voice(total_seconds)
        observe_llm_ttft(ttft_seconds, "primary", "reply")

    @staticmethod
    def record_guard_intervention(rule_name: str = "unauthorized_number"):
        record_guard_intervention(rule_name)

    @staticmethod
    def record_opt_out():
        record_opt_out()
