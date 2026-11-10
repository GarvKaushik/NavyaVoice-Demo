"""SQLAlchemy ORM models defining the schema for the Hinglish Voice Agent.

Postgres is the system of record. All timestamps are naive UTC.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    JSON,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator

from config.settings import get_settings
from .session import Base

try:
    from pgvector.sqlalchemy import Vector as _PgVector
except ImportError:  # pragma: no cover
    _PgVector = None

EMBEDDING_DIM = get_settings().EMBEDDING_DIM

DEFAULT_CLIENT_ID = "CLIENT-DEMO"  # owner of the seed content and of data created before clients existed
GLOBAL_DND_CLIENT = "*"  # dnd_registry rows that apply to every client (national DND registry)


def utcnow() -> datetime:
    """Naive UTC timestamp used for every DateTime column."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EmbeddingVector(TypeDecorator):
    """pgvector on Postgres, JSON list elsewhere (SQLite tests)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql" and _PgVector is not None:
            return dialect.type_descriptor(_PgVector(EMBEDDING_DIM))
        return dialect.type_descriptor(JSON())

    def process_result_value(self, value: Any, dialect) -> Optional[List[float]]:
        if value is None:
            return None
        return [float(x) for x in value]


# ------------------------------------------------------------------------------
# Clients, users and billing
# ------------------------------------------------------------------------------
class Plan(Base):
    """Commercial plan. The billing rule lives here because it may change."""

    __tablename__ = "plans"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    included_minutes = Column(Integer, default=0)
    rate_per_minute_inr = Column(Float, default=0.0)
    billing_increment_seconds = Column(Integer, default=1)  # 1 = exact answered time, 60 = round up to whole minutes
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class Client(Base):
    """A company using the platform. Everything it owns carries its client_id."""

    __tablename__ = "clients"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    status = Column(String(16), default="active", index=True)  # active | suspended
    plan_id = Column(String(64), ForeignKey("plans.id"), nullable=True)
    balance_seconds = Column(Integer, default=0)  # kept in step with minute_ledger
    max_concurrent_calls = Column(Integer, default=5)

    # What callers hear
    company_name = Column(String(128), nullable=False)
    agent_persona_name = Column(String(64), default="Priya")
    complaint_contact = Column(String(128), nullable=True)
    voice_id = Column(String(32), nullable=True)

    # Telephony and integrations
    caller_id = Column(String(20), nullable=True)  # the client's one assigned number
    transfer_numbers = Column(JSON, nullable=True)  # sales desk numbers for warm transfers
    payment_links_enabled = Column(Boolean, default=False)  # payment links settle to one shared Razorpay account
    crm_webhook_url = Column(String(512), nullable=True)
    crm_webhook_secret = Column(String(128), nullable=True)

    default_stage_prompts = Column(JSON, nullable=True)  # copied into each new campaign's playbook
    default_objection_playbooks = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class User(Base):
    """Portal login. Admins have no client_id; client users see only their client."""

    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    email = Column(String(254), unique=True, nullable=False)
    name = Column(String(128), nullable=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False)  # admin | client
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=True)
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class MinuteLedger(Base):
    """Every change to a client's balance. A call is debited at most once (unique call_id)."""

    __tablename__ = "minute_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    entry_type = Column(String(16), nullable=False)  # allocation | top_up | adjustment | call
    seconds = Column(Integer, nullable=False)  # positive adds minutes, negative uses them
    balance_after_seconds = Column(Integer, nullable=False)
    call_id = Column(String(64), unique=True, nullable=True)
    answered_seconds = Column(Integer, nullable=True)
    rate_per_minute_inr = Column(Float, nullable=True)
    note = Column(String(256), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)


class CreditRequest(Base):
    """A client asking for more minutes; an admin decides. Payment is handled outside the system."""

    __tablename__ = "credit_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    minutes = Column(Integer, nullable=False)
    note = Column(String(512), nullable=True)
    status = Column(String(16), default="pending", index=True)  # pending | approved | rejected
    requested_by = Column(String(64), nullable=True)
    decided_by = Column(String(64), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AuditLog(Base):
    """Admin and security-relevant actions."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_id = Column(String(64), nullable=True)
    actor_email = Column(String(254), nullable=True)
    action = Column(String(64), nullable=False, index=True)
    client_id = Column(String(64), index=True, nullable=True)
    target = Column(String(128), nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)


# ------------------------------------------------------------------------------
# Campaigns
# ------------------------------------------------------------------------------
class Campaign(Base):
    """A calling campaign: which products, which script, which voice, which window."""

    __tablename__ = "campaigns"

    id = Column(String(64), primary_key=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    name = Column(String(128), nullable=False)
    product_skus = Column(JSON, nullable=False, default=list)
    script_version = Column(String(32), nullable=True)
    is_promotional = Column(Boolean, default=True)
    status = Column(String(16), default="draft", index=True)  # draft | active | paused | completed
    voice_id = Column(String(32), nullable=True)
    voice_pitch = Column(Float, default=0.0)
    voice_pace = Column(Float, default=1.0)
    calling_start_hour = Column(Integer, nullable=True)
    calling_end_hour = Column(Integer, nullable=True)
    max_attempts = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# ------------------------------------------------------------------------------
# Catalog & knowledge (admin changes, versioned)
# ------------------------------------------------------------------------------
class CatalogItem(Base):
    """Product catalog item with validated pricing and approved phrasing."""

    __tablename__ = "catalog_items"

    client_id = Column(String(64), ForeignKey("clients.id"), primary_key=True)
    sku = Column(String(64), primary_key=True)
    plan = Column(String(64), primary_key=True)
    version = Column(Integer, primary_key=True, default=1)
    name = Column(String(128), nullable=False)
    price_inr = Column(Float, nullable=False)
    billing_period = Column(String(32), default="monthly")
    description = Column(Text, nullable=True)
    features = Column(JSON, nullable=True)
    approved_phrasing = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Offer(Base):
    """Targeted discounts and promotional campaigns with validity caps."""

    __tablename__ = "offers"

    id = Column(String(64), primary_key=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    campaign_id = Column(String(64), index=True, nullable=True)
    customer_id = Column(String(64), index=True, nullable=True)  # lead-specific offer
    eligible_segments = Column(JSON, nullable=True)  # null = every segment
    sku = Column(String(64), nullable=False)
    plan = Column(String(64), nullable=False)
    discount_percentage = Column(Float, default=0.0)
    discounted_price_inr = Column(Float, nullable=False)
    approved_phrasing = Column(Text, nullable=True)
    valid_from = Column(DateTime, default=utcnow)
    valid_until = Column(DateTime, nullable=False)
    max_uses = Column(Integer, default=1)
    current_uses = Column(Integer, default=0)
    per_lead_cap = Column(Integer, default=1)
    is_active = Column(Boolean, default=True, index=True)


class Availability(Base):
    """Nightly availability / onboarding snapshot by pincode prefix."""

    __tablename__ = "availability"

    client_id = Column(String(64), ForeignKey("clients.id"), primary_key=True)
    sku = Column(String(64), primary_key=True)
    pincode_prefix = Column(String(6), primary_key=True)  # "" = nationwide default
    available = Column(Boolean, default=True)
    eta_text = Column(String(128), nullable=False)  # approved phrasing, e.g. "10 minute mein online activation"
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Policy(Base):
    """Approved legal, cancellation, and refund policies."""

    __tablename__ = "policies"

    client_id = Column(String(64), ForeignKey("clients.id"), primary_key=True)
    topic = Column(String(64), primary_key=True)
    version = Column(String(32), primary_key=True)
    title = Column(String(128), nullable=False)
    approved_text = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=utcnow)


class KnowledgeDocument(Base):
    """An uploaded document. Its chunks reach calls only after an admin approves it."""

    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    campaign_id = Column(String(64), nullable=True)  # null = every campaign of the client
    filename = Column(String(256), nullable=False)
    chunk_count = Column(Integer, default=0)
    status = Column(String(16), default="pending", index=True)  # pending | approved | rejected
    uploaded_by = Column(String(64), nullable=True)
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class FAQ(Base):
    """Long-tail FAQ chunks, queried only when no structured tool or playbook fits."""

    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    document_id = Column(Integer, ForeignKey("knowledge_documents.id"), index=True, nullable=True)
    campaign_id = Column(String(64), index=True, nullable=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    topic = Column(String(64), index=True)
    embedding = Column(EmbeddingVector, nullable=True)
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Playbook(Base):
    """Stage prompts and objection answers; only approved releases reach calls."""

    __tablename__ = "playbooks"

    script_version = Column(String(32), primary_key=True)
    campaign_id = Column(String(64), primary_key=True)
    stage_prompts = Column(JSON, nullable=False)
    objection_playbooks = Column(JSON, nullable=False)
    status = Column(String(16), default="draft", index=True)  # draft | approved | retired
    source = Column(String(32), default="human")  # human | offline_miner
    notes = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    approved_by = Column(String(64), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    is_active = Column(Boolean, default=True)


# ------------------------------------------------------------------------------
# Leads, consent and DND
# ------------------------------------------------------------------------------
class Lead(Base):
    """Prospective customer lead profile and outreach history."""

    __tablename__ = "leads"

    id = Column(String(64), primary_key=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    campaign_id = Column(String(64), ForeignKey("campaigns.id"), index=True, nullable=True)
    phone_number = Column(String(20), index=True, nullable=False)
    full_name = Column(String(128), nullable=False)
    preferred_language = Column(String(16), default="hinglish")
    timezone = Column(String(64), default="Asia/Kolkata")
    segment = Column(String(64), nullable=True)
    consent_source = Column(String(128), nullable=False)  # e.g. "Website Form 2026-11-01"
    consent_at = Column(DateTime, nullable=True)
    consent_notice_version = Column(String(32), nullable=True)
    is_dnd = Column(Boolean, default=False, index=True)
    dnd_source = Column(String(32), nullable=True)
    dlt_scrubbed_at = Column(DateTime, nullable=True)
    dlt_registered_dnd = Column(Boolean, default=False)
    attempt_count = Column(Integer, default=0)
    status = Column(String(16), default="pending", index=True)  # pending | in_progress | done | opted_out | exhausted
    last_outcome = Column(String(64), nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    calls = relationship("CallRecord", back_populates="lead", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("client_id", "phone_number", name="uq_leads_client_phone"),)


class DNDEntry(Base):
    """Permanent do-not-call registry, per client (opt-outs, complaints).
    Rows under GLOBAL_DND_CLIENT apply to every client (national DND registry)."""

    __tablename__ = "dnd_registry"

    client_id = Column(String(64), primary_key=True)
    phone_number = Column(String(20), primary_key=True)
    source = Column(String(32), nullable=False)  # opt_out | dlt | manual | complaint
    reason = Column(String(256), nullable=True)
    call_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ConsentRecord(Base):
    """Regulatory audit trail for TRAI & DPDP Act compliance."""

    __tablename__ = "consent_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    lead_id = Column(String(64), ForeignKey("leads.id"), index=True, nullable=False)
    phone_number = Column(String(20), index=True, nullable=False)
    call_id = Column(String(64), index=True, nullable=True)
    event = Column(String(32), default="consent_captured")  # consent_captured | disclosure_played | opt_out
    consent_source = Column(String(128), nullable=False)
    notice_version = Column(String(32), nullable=True)
    disclosure_played = Column(Boolean, default=False)
    disclosure_played_at = Column(DateTime, nullable=True)
    opted_out = Column(Boolean, default=False)
    opt_out_timestamp = Column(DateTime, nullable=True)
    opt_out_channel = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Complaint(Base):
    """Customer complaints raised on a call or through the published channel."""

    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    phone_number = Column(String(20), index=True, nullable=False)
    lead_id = Column(String(64), nullable=True)
    call_id = Column(String(64), nullable=True)
    channel = Column(String(32), default="voice")
    description = Column(Text, nullable=True)
    status = Column(String(16), default="open", index=True)  # open | resolved
    created_at = Column(DateTime, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)


# ------------------------------------------------------------------------------
# Dialing queue (Postgres job queue)
# ------------------------------------------------------------------------------
class DialJob(Base):
    """One scheduled dial attempt; claimed with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "dial_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    lead_id = Column(String(64), ForeignKey("leads.id"), index=True, nullable=False)
    campaign_id = Column(String(64), index=True, nullable=False)
    scheduled_at = Column(DateTime, nullable=False, default=utcnow)
    status = Column(String(16), default="queued")  # queued | dialing | done | failed | cancelled | manual
    reason = Column(String(32), default="initial")  # initial | retry | callback | human_expert_callback
    attempt_number = Column(Integer, default=1)
    call_id = Column(String(64), nullable=True)
    locked_by = Column(String(64), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (Index("idx_dial_jobs_due", "client_id", "status", "scheduled_at"),)


# ------------------------------------------------------------------------------
# Calls
# ------------------------------------------------------------------------------
class CallRecord(Base):
    """Detailed telemetry, outcome, and cost ledger for each call."""

    __tablename__ = "call_records"

    call_id = Column(String(64), primary_key=True)  # also the LiveKit room name
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    lead_id = Column(String(64), ForeignKey("leads.id"), index=True, nullable=False)
    campaign_id = Column(String(64), index=True, nullable=False)
    direction = Column(String(8), default="outbound")
    caller_id_used = Column(String(20), nullable=False)
    script_version = Column(String(32), nullable=True)
    voice_id = Column(String(32), nullable=True)
    status = Column(String(16), default="dialing", index=True)  # dialing | in_progress | ended
    start_time = Column(DateTime, default=utcnow)
    answered_at = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, default=0)
    billed_minutes = Column(Float, default=0)  # carrier pulses, used for our cost
    client_billed_seconds = Column(Integer, default=0)  # what the client's balance was charged

    # interested | payment_link_sent | demo_booked | transferred | manager_callback | opted_out | not_interested |
    # callback_scheduled | busy | no_answer | voicemail | wrong_person | dropped | failed | abusive | unclear
    outcome = Column(String(64), default="in_progress")
    outcome_detail = Column(Text, nullable=True)
    stage_reached = Column(String(64), default="OPENING")
    disclosure_played = Column(Boolean, default=False)

    # Financial cost ledger in INR
    cost_telephony_inr = Column(Float, default=0.0)
    cost_stt_inr = Column(Float, default=0.0)
    cost_tts_inr = Column(Float, default=0.0)
    cost_llm_inr = Column(Float, default=0.0)
    cost_orchestration_inr = Column(Float, default=0.0)
    cost_infra_inr = Column(Float, default=0.0)
    cost_contingency_inr = Column(Float, default=0.0)
    cost_total_inr = Column(Float, default=0.0)

    # Usage meters
    stt_audio_seconds = Column(Float, default=0.0)
    tts_characters = Column(Integer, default=0)
    tts_cached_characters = Column(Integer, default=0)
    llm_input_tokens = Column(Integer, default=0)
    llm_cached_tokens = Column(Integer, default=0)
    llm_output_tokens = Column(Integer, default=0)
    turn_count = Column(Integer, default=0)
    guard_replacements = Column(Integer, default=0)
    false_interruptions = Column(Integer, default=0)
    agent_talk_share = Column(Float, nullable=True)
    p50_latency_ms = Column(Float, nullable=True)
    p95_latency_ms = Column(Float, nullable=True)

    transcript = Column(JSON, nullable=True)
    call_state = Column(JSON, nullable=True)
    recording_url = Column(String(512), nullable=True)
    recording_deleted_at = Column(DateTime, nullable=True)
    summary = Column(JSON, nullable=True)
    qa_scorecard = Column(JSON, nullable=True)
    analyzed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)

    lead = relationship("Lead", back_populates="calls")
    events = relationship("CallEvent", back_populates="call", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_call_records_client_start", "client_id", "start_time"),)


class CallEvent(Base):
    """Per-turn latency, transcripts, tool calls, and guard interventions."""

    __tablename__ = "call_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(String(64), ForeignKey("call_records.call_id"), index=True, nullable=False)
    turn_number = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=utcnow)
    # transcript | agent_reply | tool_call | guard_intervention | fast_path | stage_change | disclosure | error
    event_type = Column(String(32), nullable=False)

    # Turn-by-turn Latency Budget Breakdown (milliseconds)
    latency_vad_ms = Column(Float, default=0.0)
    latency_stt_ms = Column(Float, default=0.0)
    latency_llm_ttft_ms = Column(Float, default=0.0)
    latency_tts_first_byte_ms = Column(Float, default=0.0)
    total_turn_latency_ms = Column(Float, default=0.0)

    details = Column(JSON, nullable=True)

    call = relationship("CallRecord", back_populates="events")

    __table_args__ = (
        Index("idx_call_event_turn", "call_id", "turn_number"),
    )


# ------------------------------------------------------------------------------
# Actions taken on calls (idempotent)
# ------------------------------------------------------------------------------
class PaymentLink(Base):
    """Payment link sent via WhatsApp or SMS. Idempotency key prevents double sends."""

    __tablename__ = "payment_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(128), unique=True, nullable=False)
    lead_id = Column(String(64), index=True, nullable=False)
    call_id = Column(String(64), index=True, nullable=True)
    sku = Column(String(64), nullable=False)
    plan = Column(String(64), nullable=False)
    offer_id = Column(String(64), nullable=True)
    amount_inr = Column(Float, nullable=False)
    channel = Column(String(16), nullable=False)
    provider_link_id = Column(String(128), nullable=True)
    url = Column(String(512), nullable=True)
    status = Column(String(16), default="created")  # created | sent | failed | paid
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class DemoSlot(Base):
    """Bookable demo slots offered by the sales team."""

    __tablename__ = "demo_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    starts_at = Column(DateTime, nullable=False, index=True)  # UTC
    duration_minutes = Column(Integer, default=15)
    capacity = Column(Integer, default=1)
    booked_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)


class DemoBooking(Base):
    __tablename__ = "demo_bookings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(128), unique=True, nullable=False)
    slot_id = Column(Integer, ForeignKey("demo_slots.id"), nullable=False)
    lead_id = Column(String(64), index=True, nullable=False)
    call_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class CRMOutbox(Base):
    """Outcome webhooks to the CRM, delivered with retries (transactional outbox)."""

    __tablename__ = "crm_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), index=True, nullable=False)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(16), default="pending", index=True)  # pending | delivered | dead | skipped
    attempts = Column(Integer, default=0)
    next_attempt_at = Column(DateTime, default=utcnow)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    delivered_at = Column(DateTime, nullable=True)
