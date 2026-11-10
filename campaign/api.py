"""FastAPI Control Plane: campaigns, leads, calls, playbooks, opt-outs and the browser test console."""

import asyncio
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.dialogue import DialogueEngine
from agent.knowledge import CampaignKnowledge, load_knowledge
from agent.tools import CallIdentity, record_opt_out
from campaign.auth import require_internal
from campaign.dialer import OutboundDialer
from campaign.operations import import_leads as add_leads
from campaign.operations import publish_playbook, start_campaign
from campaign.portal_api import router as portal_router
from config.settings import get_settings
from database.models import CallEvent, CallRecord, Campaign, DEFAULT_CLIENT_ID, Lead, utcnow
from database.session import get_db_session
from llm.client import LLMClient
from observability.logging_setup import setup_logging
from observability.metrics import CostCalculator
from speech.bulbul_tts import BulbulTTSError, pcm_to_wav, shared_http
from speech.openai_tts import create_tts_client
from speech.phrase_cache import PreRenderedPhraseCache

import base64
import json
import logging

settings = get_settings()
setup_logging("control-api")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Hinglish AI Voice Agent - Control API",
    version="2.0.0",
    description="Campaigns, leads, calls, playbooks, compliance and the browser test console.",
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

dialer = OutboundDialer()
app.include_router(portal_router)


# ------------------------------------------------------------------------------
# Health & metrics
# ------------------------------------------------------------------------------
@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "healthy", "service": "voice-agent-control-api", "environment": settings.ENVIRONMENT,
            "llm_model": settings.OPENAI_MODEL if settings.LLM_PROVIDER == "openai" else settings.REALTIME_LLM_MODEL,
            "tts": f"{settings.tts_model}/{settings.tts_voice}",
            "stt": settings.STT_PROVIDER if settings.STT_PROVIDER != "sarvam" else f"{settings.SARVAM_STT_MODEL}/{settings.SARVAM_STT_MODE}",
            "stt_provider": settings.STT_PROVIDER, "llm_provider": settings.LLM_PROVIDER}


@app.get("/metrics", tags=["System"])
def prometheus_metrics():
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ------------------------------------------------------------------------------
# Leads & campaigns
# ------------------------------------------------------------------------------
class LeadImportItem(BaseModel):
    phone_number: str = Field(..., examples=["+919876543210"])
    full_name: str = Field(..., examples=["Amit Verma"])
    consent_source: str = Field(..., examples=["Web Landing Page 2026-11-03"])
    preferred_language: str = "hinglish"
    timezone: str = "Asia/Kolkata"
    segment: Optional[str] = None


class LeadImportRequest(BaseModel):
    leads: List[LeadImportItem]


class CampaignTriggerRequest(BaseModel):
    batch_size: int = Field(default=50, le=500)
    is_promotional: bool = True


class TestCallRequest(BaseModel):
    phone_number: str
    customer_name: str = "ji"
    campaign_id: str = "CAMP-REENGAGE-Q4"
    ignore_calling_window: bool = False


@app.post("/campaigns/{campaign_id}/leads", tags=["Campaigns"], dependencies=[Depends(require_internal)])
async def import_leads(campaign_id: str, payload: LeadImportRequest, db: AsyncSession = Depends(get_db_session)):
    """Imports leads; consent source is mandatory (DPDP Act) and recorded per lead."""
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    result = await add_leads(db, campaign.client_id, campaign_id, payload.leads)
    await db.commit()
    return {"status": "success", "campaign_id": campaign_id, **result}


@app.post("/campaigns/{campaign_id}/start", tags=["Campaigns"], dependencies=[Depends(require_internal)])
async def trigger_campaign(campaign_id: str, payload: CampaignTriggerRequest, db: AsyncSession = Depends(get_db_session)):
    """Queues eligible leads for the dialer service (each passes compliance checks before dialing)."""
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    campaign.is_promotional = payload.is_promotional
    queued = await start_campaign(db, campaign, payload.batch_size)
    await db.commit()
    if not queued:
        return {"status": "no_eligible_leads", "campaign_id": campaign_id}
    return {"status": "campaign_triggered", "campaign_id": campaign_id, "leads_queued": queued}


@app.post("/calls/test", tags=["Calls"], dependencies=[Depends(require_internal)])
async def place_test_call(req: TestCallRequest, db: AsyncSession = Depends(get_db_session)):
    """Places one real outbound call to a number (e.g. your own phone) through LiveKit SIP + Plivo."""
    if not settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
        raise HTTPException(400, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID not set; run scripts/provision_livekit_sip.py first")
    campaign = await db.get(Campaign, req.campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    lead = (await db.execute(select(Lead).where(Lead.client_id == campaign.client_id,
                                                Lead.phone_number == req.phone_number))).scalar()
    if lead is None:
        lead = Lead(id=f"lead_{uuid.uuid4().hex[:10]}", client_id=campaign.client_id, campaign_id=req.campaign_id,
                    phone_number=req.phone_number,
                    full_name=req.customer_name, consent_source="internal test call", consent_at=utcnow())
        db.add(lead)
        await db.commit()
    return await dialer.dial_single_lead(lead.id, lead.phone_number, 0, req.campaign_id, True, req.customer_name,
                                         lead.timezone, lead.segment, skip_window_check=req.ignore_calling_window,
                                         client_id=campaign.client_id)


@app.get("/calls", tags=["Calls"], dependencies=[Depends(require_internal)])
async def list_calls(limit: int = 20, db: AsyncSession = Depends(get_db_session)):
    """Operator view across all clients, including internal costs. Clients use /api/client/calls."""
    rows = (await db.execute(select(CallRecord).order_by(desc(CallRecord.start_time)).limit(limit))).scalars().all()
    return [{"call_id": r.call_id, "lead_id": r.lead_id, "direction": r.direction, "status": r.status,
             "outcome": r.outcome, "stage_reached": r.stage_reached, "duration_seconds": r.duration_seconds,
             "cost_total_inr": r.cost_total_inr, "p50_latency_ms": r.p50_latency_ms, "turns": r.turn_count,
             "guard_replacements": r.guard_replacements, "start_time": r.start_time} for r in rows]


@app.get("/calls/{call_id}", tags=["Calls"], dependencies=[Depends(require_internal)])
async def get_call(call_id: str, db: AsyncSession = Depends(get_db_session)):
    r = await db.get(CallRecord, call_id)
    if r is None:
        raise HTTPException(404, "call not found")
    events = (await db.execute(select(CallEvent).where(CallEvent.call_id == call_id).order_by(CallEvent.id))).scalars().all()
    data = {c.name: getattr(r, c.name) for c in CallRecord.__table__.columns}
    data["events"] = [{"turn": e.turn_number, "type": e.event_type, "details": e.details, "at": e.timestamp} for e in events]
    return data


class OptOutRequest(BaseModel):
    phone_number: str
    client_id: str = DEFAULT_CLIENT_ID
    channel: str = "api"


@app.post("/opt-outs", tags=["Compliance"], dependencies=[Depends(require_internal)])
async def api_opt_out(req: OptOutRequest, db: AsyncSession = Depends(get_db_session)):
    """Records an opt-out received outside a call (SMS STOP, email, complaint desk)."""
    lead = (await db.execute(select(Lead).where(Lead.client_id == req.client_id,
                                                Lead.phone_number == req.phone_number))).scalar()
    identity = CallIdentity(call_id="", lead_id=lead.id if lead else "unknown", campaign_id="",
                            phone_number=req.phone_number, customer_name="", client_id=req.client_id)
    await record_opt_out(identity, channel=req.channel)
    return {"status": "opted_out", "phone_number": req.phone_number}


# ------------------------------------------------------------------------------
# Playbooks / prompt editor (versioned; edits create a new approved version)
# ------------------------------------------------------------------------------
class PromptsUpdateRequest(BaseModel):
    stage_prompts: Optional[Dict[str, str]] = None
    objection_playbooks: Optional[Dict[str, str]] = None
    campaign_id: str = "CAMP-REENGAGE-Q4"
    approved_by: str = "web-console"


STAGE_ORDER = ["OPENING", "DISCOVERY", "NEED_IDENTIFICATION", "PITCH", "OBJECTION_HANDLING", "CLOSE_NEXT_STEP"]


@app.get("/api/prompts", tags=["Prompt Editor"], dependencies=[Depends(require_internal)])
async def get_prompts(campaign_id: str = "CAMP-REENGAGE-Q4"):
    from agent.stages import StageManager, SalesStage

    k = await _knowledge(campaign_id, refresh=True)
    guidance = StageManager()
    out = []
    for stage in STAGE_ORDER:
        guidance.current_stage = SalesStage(stage)
        out.append({"stage": stage, "guidance": guidance.get_stage_guidance(), "prompt": k.stage_prompts.get(stage, "")})
    return {"script_version": k.script_version, "source": k.source, "stage_prompts": out,
            "objection_playbooks": [{"type": t, "script": s} for t, s in k.objection_playbooks.items()]}


@app.post("/api/prompts", tags=["Prompt Editor"], dependencies=[Depends(require_internal)])
async def update_prompts(payload: PromptsUpdateRequest, db: AsyncSession = Depends(get_db_session)):
    """Saves edits as a new approved playbook version; new calls and console sessions pick it up."""
    k = await _knowledge(payload.campaign_id, refresh=True)
    stage_prompts = {**k.stage_prompts, **{s: p for s, p in (payload.stage_prompts or {}).items() if s in STAGE_ORDER}}
    objections = {**k.objection_playbooks, **(payload.objection_playbooks or {})}
    campaign = await db.get(Campaign, payload.campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    version = await publish_playbook(db, campaign, stage_prompts, objections, payload.approved_by)
    await db.commit()
    _knowledge_cache.pop(payload.campaign_id, None)
    return {"status": "updated", "script_version": version}


_knowledge_cache: Dict[str, tuple] = {}


async def _knowledge(campaign_id: str, refresh: bool = False) -> CampaignKnowledge:
    cached = _knowledge_cache.get(campaign_id)
    if cached and not refresh and time.time() - cached[1] < 60:
        return cached[0]
    k = await load_knowledge(campaign_id)
    _knowledge_cache[campaign_id] = (k, time.time())
    return k


# ------------------------------------------------------------------------------
# Browser test console: same DialogueEngine as phone calls, same STT and TTS providers
# ------------------------------------------------------------------------------
class ConsoleSession:
    def __init__(self, engine: DialogueEngine):
        self.engine = engine
        self.started = time.time()
        self.tts_chars = 0
        self.last_used = time.time()


_sessions: Dict[str, ConsoleSession] = {}
_llm: Optional[LLMClient] = None
_tts = create_tts_client()
_phrases = PreRenderedPhraseCache()
_phrases.load()
DEFAULT_SESSION = "default"


class UserTurnRequest(BaseModel):
    transcript: str


def _require_console() -> None:
    if not settings.WEB_CONSOLE_ENABLED:
        raise HTTPException(404, "web console disabled")


def _session_id(x_session_id: Optional[str]) -> str:
    return (x_session_id or DEFAULT_SESSION)[:64]


async def _audio_for(utterances) -> Optional[str]:
    """One WAV for the turn: cached phrases from disk, the rest synthesized live."""
    async def one(u) -> bytes:
        cached = _phrases.get_audio(u.phrase_id) if u.phrase_id else None
        return cached if cached else await _tts.synthesize_pcm(u.text)

    try:
        parts = await asyncio.gather(*(one(u) for u in utterances))  # sentences synthesized in parallel
    except Exception as e:
        logger.warning(f"Console TTS failed: {e}")
        return None
    pcm = b"".join(parts)
    return base64.b64encode(pcm_to_wav(bytes(pcm), _tts.sample_rate)).decode() if pcm else None


def _response(s: ConsoleSession, utterances, t0: float, fast_intent: Optional[str], audio: Optional[str]) -> Dict[str, Any]:
    e = s.engine
    tool_result = e.tools.tool_log[-1]["result"] if e.tools.tool_log else None
    guard_events = [u for u in utterances if u.kind == "guard_replacement"]
    duration = max(int(time.time() - s.started), 1)
    cost = CostCalculator.compute_call_cost(duration_seconds=duration, stt_audio_seconds=duration * 0.4,
                                            tts_character_count=s.tts_chars, llm_input_tokens=e.usage.input_tokens,
                                            llm_cached_tokens=e.usage.cached_tokens,
                                            llm_output_tokens=e.usage.output_tokens)
    return {
        "agent_reply": " ".join(u.text for u in utterances),
        "audio_base64": audio,
        "current_stage": e.stage.current_stage.value,
        "endpoint_delay_ms": e.stage.endpointing_delay_ms,
        "guard_status": {"passed": not guard_events, "authorized_numbers": [str(n) for n in sorted(e.auth.numbers)],
                         "reasons": [r for u in guard_events for r in u.guard_reasons]},
        "fast_path_triggered": fast_intent is not None,
        "fast_path_intent": fast_intent,
        "tool_result": tool_result,
        "latency": {"total_turn_ms": round((time.monotonic() - t0) * 1000, 1),
                    "llm_ttft_ms": e.last_turn_metrics.get("llm_ttft_ms")},
        "cost": {**cost.__dict__, "plivo_inr": cost.telephony_inr, "sarvam_stt_inr": cost.stt_inr,
                 "sarvam_tts_inr": cost.tts_inr},
        "utterances": [{"text": u.text, "kind": u.kind, "cached": bool(u.phrase_id)} for u in utterances],
        "call_ended": e.pending_action is not None,
        "outcome": e.outcome,
    }


@app.get("/api/voice/init", tags=["Web Console"], dependencies=[Depends(require_internal)])
async def init_voice_session(customer_name: str = "Rajesh", campaign_id: str = "CAMP-REENGAGE-Q4",
                             x_session_id: Optional[str] = Header(default=None)):
    """Starts a console conversation: pre-rendered disclosure + right-person check."""
    _require_console()
    global _llm
    _llm = _llm or LLMClient.realtime()
    sid = _session_id(x_session_id)
    knowledge = await _knowledge(campaign_id)
    identity = CallIdentity(call_id=f"web_{uuid.uuid4().hex[:8]}", lead_id="LEAD-TEST-001", campaign_id=campaign_id,
                            phone_number="+919876543210", customer_name=customer_name, client_id=knowledge.client_id)
    engine = DialogueEngine(identity, knowledge, _llm, observer_llm=_llm)
    s = ConsoleSession(engine)
    _sessions[sid] = s
    t0 = time.monotonic()
    utterances = engine.opening()
    s.tts_chars += sum(len(u.text) for u in utterances if not u.phrase_id)
    return _response(s, utterances, t0, None, await _audio_for(utterances))


@app.post("/api/voice/turn", tags=["Web Console"], dependencies=[Depends(require_internal)])
async def process_voice_turn(turn: UserTurnRequest, x_session_id: Optional[str] = Header(default=None)):
    """Runs one caller turn through the full engine and returns the reply with audio."""
    _require_console()
    s = _sessions.get(_session_id(x_session_id))
    if s is None:
        raise HTTPException(409, "No active session; call /api/voice/init first")
    e = s.engine
    t0 = time.monotonic()
    if e.pending_action is not None:
        return _response(s, [], t0, None, None)
    scripted = await e.pre_turn(turn.transcript)
    if scripted is not None:
        utterances, fast_intent = scripted.utterances, scripted.intent
    else:
        utterances = [u async for u in e.stream_reply()]
        fast_intent = None
    s.tts_chars += sum(len(u.text) for u in utterances if not u.phrase_id)
    return _response(s, utterances, t0, fast_intent, await _audio_for(utterances))


async def _local_stt(audio: bytes) -> str:
    """Sends a browser WAV to the local STT server."""
    import io
    import wave

    from speech.local_stt import ServerDecodeError, decode_on_server

    with wave.open(io.BytesIO(audio), "rb") as w:
        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    try:
        return await decode_on_server(settings.STT_PROVIDER, pcm, rate)
    except ServerDecodeError as e:
        raise HTTPException(502, f"Local STT error: {e}. Is the STT server running? (python -m speech.stt_server)")


async def _transcribe(audio: bytes) -> str:
    if settings.STT_PROVIDER != "sarvam":
        return await _local_stt(audio)
    resp = await shared_http().post(
        settings.SARVAM_STT_URL,
        headers={"api-subscription-key": settings.SARVAM_API_KEY},
        data={"model": settings.SARVAM_STT_MODEL, "mode": settings.SARVAM_STT_MODE,
              "language_code": settings.SARVAM_STT_LANGUAGE_CODE},
        files={"file": ("utterance.wav", audio, "audio/wav")},
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"Sarvam STT error {resp.status_code}: {resp.text[:300]}")
    return (resp.json().get("transcript") or "").strip()


@app.post("/api/voice/stt", tags=["Web Console"], dependencies=[Depends(require_internal)])
async def console_stt(request: Request):
    """Transcribes one recorded utterance (WAV body) with the same STT provider as phone calls."""
    _require_console()
    audio = await request.body()
    if len(audio) < 1000:
        return {"transcript": "", "latency_ms": 0}
    t0 = time.monotonic()
    transcript = await _transcribe(audio)
    return {"transcript": transcript, "latency_ms": round((time.monotonic() - t0) * 1000)}


@app.post("/api/voice/turn_audio", tags=["Web Console"], dependencies=[Depends(require_internal)])
async def process_audio_turn(request: Request, x_session_id: Optional[str] = Header(default=None)):
    """One caller turn from recorded audio, streamed as NDJSON like a live call:
    transcript -> each reply sentence with its audio as soon as it is synthesized -> done (metrics)."""
    _require_console()
    s = _sessions.get(_session_id(x_session_id))
    if s is None:
        raise HTTPException(409, "No active session; call /api/voice/init first")
    audio = await request.body()

    async def stream():
        e = s.engine
        t0 = time.monotonic()
        line = lambda obj: (json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode()  # noqa: E731

        transcript = await _transcribe(audio) if len(audio) > 1000 else ""
        stt_ms = round((time.monotonic() - t0) * 1000)
        yield line({"type": "transcript", "text": transcript, "stt_ms": stt_ms})
        if not transcript or e.pending_action is not None:
            yield line({"type": "done", "empty": not transcript, "call_ended": e.pending_action is not None})
            return

        # Producer: dialogue engine; each sentence's TTS starts the moment the sentence exists
        queue: asyncio.Queue = asyncio.Queue()
        utterances: list = []
        fast_intent: Optional[str] = None

        async def produce():
            nonlocal fast_intent
            try:
                scripted = await e.pre_turn(transcript)
                if scripted is not None:
                    fast_intent = scripted.intent
                    source = scripted.utterances
                    for u in source:
                        await queue.put((u, asyncio.create_task(_audio_for([u]))))
                else:
                    async for u in e.stream_reply():
                        await queue.put((u, asyncio.create_task(_audio_for([u]))))
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        first_audio_ms = None
        while True:
            item = await queue.get()
            if item is None:
                break
            u, task = item
            audio_b64 = await task
            utterances.append(u)
            if not u.phrase_id:
                s.tts_chars += len(u.text)
            if first_audio_ms is None:
                first_audio_ms = round((time.monotonic() - t0) * 1000)
            yield line({"type": "sentence", "text": u.text, "kind": u.kind, "cached": bool(u.phrase_id),
                        "guard_reasons": u.guard_reasons, "audio_base64": audio_b64})
        await producer

        meta = _response(s, utterances, t0, fast_intent, None)
        meta.pop("audio_base64", None)
        meta["latency"].update({"stt_ms": stt_ms, "first_audio_ms": first_audio_ms})
        yield line({"type": "done", **meta})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/tts", tags=["Web Console"], dependencies=[Depends(require_internal)])
async def tts_preview(body: Dict[str, str]):
    """Synthesizes any text with the campaign voice (for listening tests). Returns audio/wav."""
    _require_console()
    try:
        return Response(await _tts.synthesize_wav(body.get("text", "")), media_type="audio/wav")
    except BulbulTTSError as e:
        raise HTTPException(502, str(e))


# ------------------------------------------------------------------------------
# React console (production build) and assets
# ------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, tags=["Web Console"])
def get_web_console():
    project_root = os.path.dirname(os.path.dirname(__file__))
    react_build = os.path.join(project_root, "frontend", "dist", "index.html")
    if os.path.exists(react_build):
        return FileResponse(react_build)
    return HTMLResponse("<h1>Frontend not built. Run: cd frontend && npm run build (or npm run dev)</h1>", status_code=404)


frontend_assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist", "assets")
if os.path.isdir(frontend_assets_dir):
    app.mount("/assets", StaticFiles(directory=frontend_assets_dir), name="frontend-assets")
