"""Application Settings and Environment Configuration.

All configuration is managed through Pydantic BaseSettings, loading from
environment variables and an optional .env file.
"""

import json
from datetime import date
from functools import lru_cache
from typing import Any, Dict, List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for telephony, real-time speech, LLMs, and databases."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # --------------------------------------------------------------------------
    # Company & Agent Persona
    # --------------------------------------------------------------------------
    COMPANY_NAME: str = Field(default="NavyaNexus", description="Company name spoken in the disclosure")
    AGENT_PERSONA_NAME: str = Field(default="Priya", description="Persona name of the AI assistant (female grammar)")
    SCRIPT_MODE: str = Field(
        default="roman",
        description="'roman' = all-Roman Hinglish, 'mixed' = Devanagari Hindi + Latin English (Phase 0 script test)",
    )
    COMPLAINT_CONTACT: str = Field(
        default="support@navyanexus.ai",
        description="Published complaint channel spoken on request and shown in API",
    )

    # --------------------------------------------------------------------------
    # Telephony Configuration (Plivo & Indian Carrier Standards)
    # --------------------------------------------------------------------------
    PLIVO_AUTH_ID: str = Field(
        default="MOCK_PLIVO_AUTH_ID",
        description="Plivo Account Auth ID used for messaging API"
    )
    PLIVO_AUTH_TOKEN: str = Field(
        default="MOCK_PLIVO_AUTH_TOKEN",
        description="Plivo Account Auth Token"
    )
    PLIVO_CALLER_ID_PROMOTIONAL: str = Field(
        default="+911401234567",
        description="140-series caller ID provisioned for promotional outbound telemarketing"
    )
    PLIVO_CALLER_ID_TRANSACTIONAL: Optional[str] = Field(
        default=None,
        description="160-series caller ID provisioned for transactional/service calls"
    )
    PLIVO_BILLING_PULSE_SECONDS: int = Field(
        default=60,
        description="Billing pulse in seconds (default 60s for India, or 1s if negotiated)"
    )
    PLIVO_RATE_PER_MINUTE_INR: float = Field(
        default=0.45,
        description="Negotiated carrier telephony rate per minute in INR"
    )
    PLIVO_MAX_CALLS_PER_SECOND: int = Field(
        default=2,
        description="Provisioned Outbound Calls Per Second (CPS) limit (Plivo default is 2)"
    )
    PLIVO_SERIES_MAX_CONCURRENT_CALLS: int = Field(
        default=50,
        description="Concurrency allocated by Plivo to the 140/160-series numbers"
    )
    PLIVO_ZENTRUNK_TERMINATION_URI: str = Field(
        default="",
        description="Plivo Zentrunk outbound termination SIP URI, e.g. 123456.zt.plivo.com"
    )
    PLIVO_ZENTRUNK_USERNAME: str = Field(default="", description="Zentrunk outbound credential username")
    PLIVO_ZENTRUNK_PASSWORD: str = Field(default="", description="Zentrunk outbound credential password")
    PLIVO_SMS_SENDER_ID: str = Field(default="", description="DLT-registered SMS header / sender ID")
    PLIVO_DLT_ENTITY_ID: str = Field(default="", description="DLT principal entity ID for SMS")
    PLIVO_DLT_PAYMENT_TEMPLATE_ID: str = Field(default="", description="DLT template ID for the payment link SMS")
    PLIVO_WHATSAPP_SENDER: str = Field(default="", description="WhatsApp Business number registered on Plivo")
    PLIVO_WHATSAPP_PAYMENT_TEMPLATE: str = Field(
        default="payment_link_v1",
        description="Approved WhatsApp template name for the payment link message",
    )

    # --------------------------------------------------------------------------
    # LiveKit Orchestration (Self-hosted in Mumbai Region)
    # --------------------------------------------------------------------------
    LIVEKIT_URL: str = Field(
        default="ws://localhost:7880",
        description="Self-hosted LiveKit Server WebSocket URL in Mumbai"
    )
    LIVEKIT_API_KEY: str = Field(
        default="devkey",
        description="LiveKit Admin API Key"
    )
    LIVEKIT_API_SECRET: str = Field(
        default="secret",
        description="LiveKit Admin API Secret"
    )
    LIVEKIT_SIP_URI: str = Field(
        default="sip.mumbai.voiceagent.internal",
        description="Load-balanced address of the LiveKit SIP service (Plivo inbound trunk points here)"
    )
    LIVEKIT_SIP_OUTBOUND_TRUNK_ID: str = Field(
        default="",
        description="LiveKit outbound SIP trunk ID created by scripts/provision_livekit_sip.py"
    )
    LIVEKIT_AGENT_NAME: str = Field(
        default="hinglish-sales-agent",
        description="Agent name used for explicit dispatch"
    )
    WORKER_MAX_SESSIONS: int = Field(
        default=20,
        description="Max concurrent calls per worker pod (~20 per 4 vCPU; measure and tune)"
    )
    WORKER_DRAIN_TIMEOUT_SECONDS: int = Field(default=900, description="Graceful drain limit on deploys")
    WORKER_METRICS_PORT: int = Field(default=9100, description="Prometheus port exposed by agent workers")

    # --------------------------------------------------------------------------
    # Speech Pipeline: Sarvam AI STT & Bulbul v4 Flash TTS
    # --------------------------------------------------------------------------
    SARVAM_API_KEY: str = Field(
        default="mock-sarvam-api-key",
        description="Sarvam AI API subscription key"
    )
    SARVAM_STT_URL: str = Field(
        default="https://api.sarvam.ai/speech-to-text",
        description="Sarvam STT REST endpoint (streaming uses the plugin's WebSocket)"
    )
    SARVAM_STT_MODEL: str = Field(default="saaras:v3", description="Sarvam streaming STT model")
    SARVAM_STT_MODE: str = Field(
        default="translit",
        description="translit = code-mixed speech transcribed in Roman script (guards and signals read Roman Hinglish)",
    )
    SARVAM_STT_FALLBACK_MODEL: str = Field(default="saaras:v4", description="Second STT model used by the fallback adapter")
    SARVAM_STT_LANGUAGE_CODE: str = Field(
        default="hi-IN",
        description="Target code-mixed Hinglish language code"
    )
    SARVAM_STT_SAMPLE_RATE: int = Field(
        default=8000,
        description="Audio sampling rate in Hz (8000 Hz mu-law native telephony standard)"
    )
    SARVAM_STT_RATE_PER_HOUR_INR: float = Field(
        default=30.0,
        description="Sarvam STT cost: ₹30 per audio hour billed per second"
    )
    SARVAM_VAD_PRE_ROLL_MS: int = Field(
        default=300,
        description="VAD pre-roll buffer in milliseconds so initial syllables are preserved"
    )

    BULBUL_TTS_URL: str = Field(
        default="https://api.sarvam.ai/text-to-speech",
        description="Sarvam Bulbul TTS REST endpoint"
    )
    BULBUL_TTS_MODEL: str = Field(
        default="bulbul:v4-flash",
        description="Low-latency Bulbul TTS model identifier"
    )
    BULBUL_TTS_FALLBACK_MODEL: str = Field(default="bulbul:v3", description="Second TTS model used by the fallback adapter")
    BULBUL_TTS_VOICE_ID: str = Field(
        default="simran_enhi_customer",
        description="Voice for the campaign (bulbul:v4-flash speakers, e.g. simran_enhi_customer, ritu_hi_sales)"
    )
    BULBUL_TTS_FALLBACK_VOICE_ID: str = Field(default="priya", description="Voice for the fallback model (bulbul:v3 speakers)")
    BULBUL_TTS_PITCH: float = Field(default=0.0, description="Voice pitch, tuned once per campaign")
    BULBUL_TTS_PACE: float = Field(default=1.0, description="Voice pace, tuned once per campaign")
    BULBUL_TTS_SAMPLE_RATE: int = Field(default=8000, description="TTS output sample rate (8 kHz, no resampling)")
    BULBUL_TTS_RATE_PER_10K_CHARS_INR: float = Field(
        default=20.0,
        description="Standard Bulbul rate: ₹20 per 10,000 characters"
    )
    BULBUL_TTS_PROMO_RATE_PER_10K_CHARS_INR: float = Field(default=10.0, description="Promotional Bulbul rate")
    BULBUL_TTS_PROMO_UNTIL: date = Field(default=date(2026, 11, 12), description="Last day of promotional pricing")
    TTS_FIRST_BYTE_TIMEOUT_SECONDS: float = Field(default=1.0, description="TTS first-byte timeout before failover")
    PHRASE_CACHE_DIR: str = Field(default="assets/phrases", description="Pre-rendered audio phrase directory")
    TTS_RUNTIME_CACHE_MAX_ITEMS: int = Field(default=2000, description="In-process cache of synthesized sentences")

    # --------------------------------------------------------------------------
    # Real-Time LLM (India-Hosted Fast Non-Reasoning Model)
    # --------------------------------------------------------------------------
    REALTIME_LLM_API_KEY: str = Field(
        default="mock-llm-api-key",
        description="API Key for the real-time LLM inference endpoint"
    )
    REALTIME_LLM_BASE_URL: str = Field(
        default="https://api.sarvam.ai/v1",
        description="OpenAI-compatible inference base URL hosted in India"
    )
    REALTIME_LLM_MODEL: str = Field(
        default="sarvam-105b-conversations",
        description="Model identifier for the real-time dialogue generator (choose by benchmark)"
    )
    REALTIME_LLM_EXTRA_BODY: str = Field(
        default='{"reasoning_effort": null}',
        description="JSON merged into every request body (e.g. disables reasoning)"
    )
    REALTIME_LLM_TEMPERATURE: float = Field(
        default=0.3,
        description="LLM temperature (low for deterministic adherence to playbooks)"
    )
    REALTIME_LLM_MAX_TOKENS: int = Field(default=160, description="Reply token cap (replies are 1-2 sentences)")
    REALTIME_LLM_TIMEOUT_SECONDS: float = Field(
        default=1.5,
        description="Maximum wait time for first token before playing a filler and failing over"
    )
    REALTIME_LLM_HARD_TIMEOUT_SECONDS: float = Field(default=6.0, description="Abandon generation after this")
    REALTIME_LLM_FALLBACK_BASE_URL: str = Field(default="", description="Second LLM vendor base URL (optional)")
    REALTIME_LLM_FALLBACK_API_KEY: str = Field(default="", description="Second LLM vendor API key")
    REALTIME_LLM_FALLBACK_MODEL: str = Field(default="", description="Second LLM vendor model")
    REALTIME_LLM_FALLBACK_EXTRA_BODY: str = Field(default="{}", description="JSON extra body for fallback LLM")
    OBSERVER_LLM_ENABLED: bool = Field(default=True, description="Run the structured-output observer per turn")
    OBSERVER_LLM_MODEL: str = Field(default="", description="Observer model (defaults to the real-time model)")
    LLM_INPUT_PRICE_PER_M_INR: float = Field(default=4.0, description="₹ per 1M input tokens")
    LLM_CACHED_INPUT_PRICE_PER_M_INR: float = Field(default=2.5, description="₹ per 1M cached input tokens")
    LLM_OUTPUT_PRICE_PER_M_INR: float = Field(default=16.0, description="₹ per 1M output tokens")

    # --------------------------------------------------------------------------
    # Offline LLM (Sarvam 105B for Post-Call Summaries & QA)
    # --------------------------------------------------------------------------
    OFFLINE_LLM_API_KEY: Optional[str] = Field(
        default=None,
        description="API Key for Sarvam 105B offline analysis"
    )
    OFFLINE_LLM_BASE_URL: str = Field(default="https://api.sarvam.ai/v1", description="Offline LLM base URL")
    OFFLINE_LLM_MODEL: str = Field(
        default="sarvam-105b",
        description="High-capacity model for offline scoring, QA, and objection mining"
    )

    # --------------------------------------------------------------------------
    # Embeddings for long-tail FAQ (pgvector); full-text search is used when unset
    # --------------------------------------------------------------------------
    EMBEDDING_BASE_URL: str = Field(default="", description="OpenAI-compatible embeddings endpoint")
    EMBEDDING_API_KEY: str = Field(default="", description="Embeddings API key")
    EMBEDDING_MODEL: str = Field(default="", description="Embeddings model")
    EMBEDDING_DIM: int = Field(default=768, description="Embedding vector dimension")

    # --------------------------------------------------------------------------
    # Database and Caching Layer (Mumbai Region)
    # --------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/voiceagent",
        description="Async PostgreSQL connection string with pgvector"
    )
    DATABASE_READ_URL: str = Field(default="", description="Read replica URL (optional)")
    DATABASE_POOL_SIZE: int = Field(
        default=20,
        description="Maximum persistent connections in the DB connection pool"
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for locks, pacing, and DND caching"
    )
    REDIS_CALL_LOCK_TTL_SECONDS: int = Field(
        default=900,
        description="TTL for single-active-call-per-number locks (15 minutes)"
    )
    CALL_EVENT_FLUSH_SECONDS: int = Field(default=30, description="Batched call-event flush interval")

    # --------------------------------------------------------------------------
    # Recording storage (encrypted object storage in an Indian region)
    # --------------------------------------------------------------------------
    RECORDING_ENABLED: bool = Field(default=False, description="Record calls via LiveKit Egress")
    RECORDING_S3_BUCKET: str = Field(default="", description="Bucket for call recordings")
    RECORDING_S3_REGION: str = Field(default="ap-south-1", description="Mumbai region")
    RECORDING_S3_ENDPOINT: str = Field(default="", description="Custom S3 endpoint (optional)")
    RECORDING_S3_ACCESS_KEY: str = Field(default="", description="S3 access key")
    RECORDING_S3_SECRET_KEY: str = Field(default="", description="S3 secret key")
    RECORDING_RETENTION_DAYS: int = Field(default=90, description="Recording retention set by the consent policy")

    # --------------------------------------------------------------------------
    # Payments, human transfer and CRM
    # --------------------------------------------------------------------------
    RAZORPAY_KEY_ID: str = Field(default="", description="Razorpay key for payment links")
    RAZORPAY_KEY_SECRET: str = Field(default="", description="Razorpay secret")
    PAYMENT_LINK_EXPIRY_HOURS: int = Field(default=72, description="Payment link validity")
    HUMAN_TRANSFER_NUMBERS: str = Field(default="", description="Comma-separated E.164 numbers of the sales desk")
    HUMAN_TRANSFER_START_HOUR: int = Field(default=10, description="Sales desk staffed from (IST)")
    HUMAN_TRANSFER_END_HOUR: int = Field(default=19, description="Sales desk staffed until (IST)")
    CRM_WEBHOOK_URL: str = Field(default="", description="CRM endpoint receiving call outcomes")
    CRM_WEBHOOK_SECRET: str = Field(default="", description="HMAC secret for webhook signatures")

    # --------------------------------------------------------------------------
    # Compliance & Calling Window (TRAI / TCCCPR Regulations)
    # --------------------------------------------------------------------------
    COMPLIANCE_CALLING_START_HOUR: int = Field(
        default=9,
        description="Allowed calling window start hour in the lead's time zone (confirm with counsel)"
    )
    COMPLIANCE_CALLING_END_HOUR: int = Field(
        default=20,
        description="Allowed calling window end hour in the lead's time zone (confirm with counsel)"
    )
    TIMEZONE: str = Field(
        default="Asia/Kolkata",
        description="Default timezone for leads without one"
    )
    MAX_DIAL_ATTEMPTS_PER_LEAD: int = Field(
        default=3,
        description="Maximum outreach retry attempts per unique lead"
    )
    DLT_SCRUB_PROVIDER_URL: str = Field(default="", description="DLT/NCPR scrubbing provider endpoint")
    DLT_SCRUB_API_KEY: str = Field(default="", description="DLT scrubbing provider key")
    DLT_SCRUB_MAX_AGE_HOURS: int = Field(default=24, description="A scrub older than this is redone before dialing")
    CONSENT_NOTICE_VERSION: str = Field(default="dpdp-notice-v1", description="DPDP notice version shown at consent")

    # --------------------------------------------------------------------------
    # Application & Control Service
    # --------------------------------------------------------------------------
    SERVICE_PORT: int = Field(
        default=8000,
        description="FastAPI service listening port"
    )
    SERVICE_HOST: str = Field(
        default="0.0.0.0",
        description="FastAPI service listening host"
    )
    ENVIRONMENT: str = Field(
        default="production",
        description="Runtime environment (development / staging / production)"
    )
    API_KEYS: str = Field(default="", description="Comma-separated keys accepted in the X-API-Key header")
    CORS_ORIGINS: str = Field(default="http://localhost:5173,http://127.0.0.1:5173", description="Allowed CORS origins")
    WEB_CONSOLE_ENABLED: bool = Field(default=True, description="Expose the browser test console endpoints")

    # --------------------------------------------------------------------------
    # Observability
    # --------------------------------------------------------------------------
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(default="", description="OTLP/HTTP endpoint (Langfuse, Tempo, collector)")
    OTEL_EXPORTER_OTLP_HEADERS: str = Field(default="", description="OTLP headers, e.g. Authorization=Basic ...")
    LOG_FORMAT: str = Field(default="json", description="'json' for central log store, 'text' for local")
    LOG_LEVEL: str = Field(default="INFO", description="Root log level")

    # --------------------------------------------------------------------------
    # Unit economics
    # --------------------------------------------------------------------------
    ORCHESTRATION_COST_PER_MIN_INR: float = Field(default=0.10, description="Self-hosted LiveKit compute")
    INFRA_COST_PER_MIN_INR: float = Field(default=0.15, description="Postgres, Redis, storage, observability, offline")
    CONTINGENCY_COST_PER_MIN_INR: float = Field(default=0.15, description="Retries, failed calls, fallbacks")
    USD_INR_RATE: float = Field(default=88.0, description="Exchange rate assumption for dollar-billed lines")

    # ----------------------------------------------------------------------
    # Derived helpers
    # ----------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def api_keys(self) -> List[str]:
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()]

    @property
    def human_transfer_numbers(self) -> List[str]:
        return [n.strip() for n in self.HUMAN_TRANSFER_NUMBERS.split(",") if n.strip()]

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @staticmethod
    def _json(value: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(value) if value else {}
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @property
    def realtime_llm_extra_body(self) -> Dict[str, Any]:
        return self._json(self.REALTIME_LLM_EXTRA_BODY)

    @property
    def fallback_llm_extra_body(self) -> Dict[str, Any]:
        return self._json(self.REALTIME_LLM_FALLBACK_EXTRA_BODY)

    @property
    def sarvam_configured(self) -> bool:
        return bool(self.SARVAM_API_KEY) and not self.SARVAM_API_KEY.startswith("mock")

    @property
    def llm_configured(self) -> bool:
        return bool(self.REALTIME_LLM_API_KEY) and not self.REALTIME_LLM_API_KEY.startswith("mock")


@lru_cache()
def get_settings() -> Settings:
    """Return cached instance of application settings."""
    return Settings()
