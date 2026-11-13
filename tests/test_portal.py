"""Portal rules: client separation, billing, credits, per-client opt-outs, dialer limits, knowledge approval."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from agent.knowledge import KnowledgeUnavailable, load_knowledge
from agent.tools import CallIdentity, CallTools, record_opt_out
from campaign.api import app
from campaign.auth import hash_password
from campaign.billing import add_minutes, billable_seconds, debit_call
from campaign.dial_service import DialService
from campaign.dialer import OutboundDialer
from campaign.knowledge_ingest import DocumentError, parse_document
from campaign.outcomes import conclusion_for
from config.settings import get_settings
from database.models import (CallRecord, Campaign, CatalogItem, Client, CRMOutbox, DialJob, DNDEntry,
                             GLOBAL_DND_CLIENT, Lead, MinuteLedger, Plan, Playbook, User, utcnow)
from database.session import AsyncSessionLocal
from guardrails.authorization import AuthorizationContext
from integrations.crm_webhook import deliver_pending, sign
from observability.call_recorder import CallRecorder
from services.redis_service import get_redis_service


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def make_client(minutes: int = 100, cap: int = 5, caller_id=None, increment: int = 1) -> str:
    client_id, plan_id = _id("client"), _id("plan")
    async with AsyncSessionLocal() as db:
        db.add(Plan(id=plan_id, name="P", rate_per_minute_inr=2.5, billing_increment_seconds=increment))
        await db.flush()
        db.add(Client(id=client_id, name=client_id, company_name=f"Company {client_id}", plan_id=plan_id,
                      balance_seconds=0, max_concurrent_calls=cap, caller_id=caller_id))
        await db.flush()
        if minutes:
            await add_minutes(db, client_id, minutes, "allocation")
        await db.commit()
    return client_id


async def make_campaign(client_id: str, status: str = "active") -> str:
    campaign_id = _id("camp")
    async with AsyncSessionLocal() as db:
        db.add(Campaign(id=campaign_id, client_id=client_id, name="C", product_skus=["X"], status=status))
        await db.merge(CatalogItem(client_id=client_id, sku="X", plan="Monthly", version=1, name=f"Product of {client_id}",
                           price_inr=500, billing_period="monthly", is_active=True))
        db.add(Playbook(script_version="v1", campaign_id=campaign_id, stage_prompts={"PITCH": "p"},
                        objection_playbooks={}, status="approved"))
        await db.commit()
    return campaign_id


async def make_lead(client_id: str, campaign_id: str, phone=None, queued=False) -> str:
    lead_id = _id("lead")
    async with AsyncSessionLocal() as db:
        db.add(Lead(id=lead_id, client_id=client_id, campaign_id=campaign_id,
                    phone_number=phone or f"+9198{uuid.uuid4().int % 10**8:08d}", full_name="L", consent_source="form"))
        await db.flush()
        if queued:
            db.add(DialJob(client_id=client_id, lead_id=lead_id, campaign_id=campaign_id))
        await db.commit()
    return lead_id


async def make_user(role: str = "client", client_id=None) -> tuple:
    email, password = f"{_id('u')}@example.test", "correct-horse-battery"
    async with AsyncSessionLocal() as db:
        db.add(User(id=_id("user"), email=email, role=role, client_id=client_id, password_hash=hash_password(password)))
        await db.commit()
    return email, password


async def signed_in(role: str = "client", client_id=None) -> httpx.AsyncClient:
    email, password = await make_user(role, client_id)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    resp = await http.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return http


async def make_call(client_id, campaign_id, lead_id, **fields) -> str:
    call_id = _id("call")
    async with AsyncSessionLocal() as db:
        db.add(CallRecord(call_id=call_id, client_id=client_id, lead_id=lead_id, campaign_id=campaign_id,
                          caller_id_used="+911", **fields))
        await db.commit()
    return call_id


# ------------------------------------------------------------------------------
# Login and client separation
# ------------------------------------------------------------------------------
async def test_login_option_must_match_the_account_type():
    customer_email, customer_password = await make_user("client", await make_client())
    admin_email, admin_password = await make_user("admin")
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    login = lambda email, password, role: http.post("/api/auth/login", json={"email": email, "password": password, "role": role})  # noqa: E731
    assert (await login(customer_email, customer_password, "admin")).status_code == 401
    assert (await login(admin_email, admin_password, "client")).status_code == 401
    assert (await login(customer_email, customer_password, "client")).json()["role"] == "client"
    assert (await login(admin_email, admin_password, "admin")).json()["role"] == "admin"


async def test_login_failure_and_lockout():
    email, password = await make_user("client", await make_client())
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    assert (await http.get("/api/client/overview")).status_code == 401
    for _ in range(5):
        assert (await http.post("/api/auth/login", json={"email": email, "password": "wrong"})).status_code == 401
    assert (await http.post("/api/auth/login", json={"email": email, "password": password})).status_code == 429


async def test_client_cannot_see_or_change_another_clients_data():
    a, b = await make_client(), await make_client()
    camp_b = await make_campaign(b)
    lead_b = await make_lead(b, camp_b)
    call_b = await make_call(b, camp_b, lead_b, status="ended", outcome="interested")
    http = await signed_in("client", a)

    assert (await http.get(f"/api/client/calls/{call_b}")).status_code == 404
    assert (await http.get("/api/client/calls")).json()["total"] == 0
    # A client user naming another client still only gets their own
    overview = (await http.get("/api/client/overview", params={"client_id": b})).json()
    assert overview["client"]["id"] == a
    assert (await http.post(f"/api/client/campaigns/{camp_b}/start")).status_code == 404
    assert (await http.post(f"/api/client/campaigns/{camp_b}/leads", json={"leads": []})).status_code == 404
    assert (await http.get("/api/admin/overview")).status_code == 403


async def test_client_responses_hide_internal_costs():
    client = await make_client()
    camp = await make_campaign(client)
    lead = await make_lead(client, camp)
    call = await make_call(client, camp, lead, status="ended", outcome="demo_booked", cost_total_inr=9.0,
                           cost_llm_inr=1.0, llm_input_tokens=99, summary={"summary": "s", "compliance": {"x": 1}},
                           qa_scorecard={"secret": True})
    http = await signed_in("client", client)
    body = (await http.get(f"/api/client/calls/{call}")).json()
    text = json.dumps(body)
    assert "cost" not in text and "llm" not in text and "compliance" not in text and "qa_scorecard" not in text
    assert body["conclusion_label"] == "Ready"


async def test_call_results_show_totals_per_campaign():
    client = await make_client()
    camp_a, camp_b = await make_campaign(client), await make_campaign(client)
    answered = utcnow()
    for outcome in ("demo_booked", "not_interested"):
        await make_call(client, camp_a, await make_lead(client, camp_a), status="ended", outcome=outcome,
                        answered_at=answered, duration_seconds=60, client_billed_seconds=60)
    await make_call(client, camp_b, await make_lead(client, camp_b), status="ended", outcome="no_answer")
    http = await signed_in("client", client)

    body = (await http.get("/api/client/calls", params={"campaign_id": camp_a})).json()
    assert body["total"] == 2 and {c["campaign_id"] for c in body["calls"]} == {camp_a}
    totals = {t["campaign_id"]: t for t in body["campaigns"]}  # every campaign, for the picker
    assert totals[camp_a] == {"campaign_id": camp_a, "calls": 2, "answered": 2, "ready": 1, "talk_seconds": 120,
                              "billed_minutes": 2.0}
    assert totals[camp_b]["calls"] == 1 and totals[camp_b]["answered"] == 0
    counts = {c["key"]: c["count"] for c in body["conclusions"]}  # only the chosen campaign
    assert counts["ready"] == 1 and counts["refused"] == 1 and counts["contact_again"] == 0


async def test_operator_endpoints_require_admin_in_production(monkeypatch):
    client = await make_client()
    settings = get_settings()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "API_KEYS", "")
    monkeypatch.setattr(settings, "AUTH_SECRET", "test-secret-long-enough-for-hs256-signing")
    anonymous = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    assert (await anonymous.get("/calls")).status_code == 401
    assert (await anonymous.get("/api/prompts")).status_code == 401
    assert (await anonymous.post("/api/tts", json={"text": "x"})).status_code == 401
    http = await signed_in("client", client)
    assert (await http.get("/calls")).status_code == 401
    admin = await signed_in("admin")
    assert (await admin.get("/calls")).status_code == 200


async def test_same_phone_can_belong_to_two_clients():
    a, b = await make_client(), await make_client()
    camp_a, camp_b = await make_campaign(a), await make_campaign(b)
    phone = "+919812345678"
    lead = {"phone_number": phone, "full_name": "Same", "consent_source": "web form"}
    http_a, http_b = await signed_in("client", a), await signed_in("client", b)
    assert (await http_a.post(f"/api/client/campaigns/{camp_a}/leads", json={"leads": [lead]})).json()["imported_leads"] == 1
    assert (await http_b.post(f"/api/client/campaigns/{camp_b}/leads", json={"leads": [lead]})).json()["imported_leads"] == 1
    again = await http_a.post(f"/api/client/campaigns/{camp_a}/leads", json={"leads": [lead]})
    assert again.json()["skipped_existing"] == 1
    bad = {"phone_number": "98123", "full_name": "Bad", "consent_source": "web form"}
    assert (await http_a.post(f"/api/client/campaigns/{camp_a}/leads", json={"leads": [bad]})).json()["invalid"] == 1


async def test_one_number_per_client():
    await make_client(caller_id="+911400000001")
    admin = await signed_in("admin")
    resp = await admin.post("/api/admin/clients", json={"name": "New Co", "company_name": "New Co",
                                                        "caller_id": "+911400000001"})
    assert resp.status_code == 409


# ------------------------------------------------------------------------------
# Per-client calling
# ------------------------------------------------------------------------------
async def test_unknown_or_unconfigured_campaign_never_gets_seed_content():
    with pytest.raises(KnowledgeUnavailable):
        await load_knowledge("CAMP-DOES-NOT-EXIST")
    client = await make_client()
    empty = _id("camp")
    async with AsyncSessionLocal() as db:
        db.add(Campaign(id=empty, client_id=client, name="No content", product_skus=[]))
        await db.commit()
    with pytest.raises(KnowledgeUnavailable):
        await load_knowledge(empty)


async def test_seed_fallback_is_off_in_production(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr("database.session.get_read_sessionmaker", broken)
    with pytest.raises(KnowledgeUnavailable):
        await load_knowledge("CAMP-REENGAGE-Q4")


async def test_campaign_knowledge_is_the_clients_own():
    a, b = await make_client(), await make_client()
    await make_campaign(b)
    camp_a = await make_campaign(a)
    k = await load_knowledge(camp_a)
    assert k.client_id == a and k.company_name == f"Company {a}"
    assert all(c.name == f"Product of {a}" for c in k.catalog.values())
    from agent.dialogue import DialogueEngine

    engine = DialogueEngine(CallIdentity("c", "l", camp_a, "+919", "R", client_id=a), k, None)
    assert f"Company {a}" in engine.opening()[0].text
    assert k.other_services == []


async def test_campaign_knows_the_clients_other_services_but_sells_only_its_own():
    client = await make_client()
    camp = await make_campaign(client)
    other = _id("client")
    async with AsyncSessionLocal() as db:
        db.add(Client(id=other, name=other, company_name="Other"))
        await db.flush()
        for owner, sku, plan, active in ((client, "Y", "Monthly", True), (client, "Y", "Annual", True),
                                         (client, "Z", "Monthly", False), (other, "W", "Monthly", True)):
            db.add(CatalogItem(client_id=owner, sku=sku, plan=plan, version=1, name=f"Service {sku}", price_inr=1,
                               billing_period="monthly", description=f"Service {sku} ek tool hai. Aur bhi.",
                               is_active=active))
        await db.commit()
    k = await load_knowledge(camp)
    assert k.other_services == ["Service Y: Service Y ek tool hai."]  # once per service, active and own only
    assert {c.sku for c in k.catalog.values()} == {"X"}  # tools still sell only the campaign's product


# ------------------------------------------------------------------------------
# Billing and credits
# ------------------------------------------------------------------------------
def test_billing_rule_comes_from_the_increment():
    assert billable_seconds(90, 1) == 90
    assert billable_seconds(90, 60) == 120
    assert billable_seconds(120, 60) == 120
    assert billable_seconds(0, 60) == 0


async def test_call_debit_uses_plan_rule_and_happens_once():
    client = await make_client(minutes=10, increment=60)
    async with AsyncSessionLocal() as db:
        assert await debit_call(db, client, "call-once", 90) == 120
        assert await debit_call(db, client, "call-once", 90) == 120  # replay charges nothing more
        await db.commit()
    async with AsyncSessionLocal() as db:
        assert (await db.get(Client, client)).balance_seconds == 600 - 120
        entries = (await db.execute(select(MinuteLedger).where(MinuteLedger.call_id == "call-once"))).scalars().all()
        assert len(entries) == 1 and entries[0].answered_seconds == 90


async def test_finalize_bills_answered_calls_only_and_marks_unclear():
    from types import SimpleNamespace
    from llm.client import Usage

    client = await make_client(minutes=10)
    camp = await make_campaign(client)
    lead = await make_lead(client, camp)
    engine = SimpleNamespace(summary=lambda: {
        "usage": Usage(), "outcome": None, "stage_reached": "PITCH", "disclosure_played": True, "turns": 2,
        "guard_replacements": 0, "transcript": [], "call_state": {}, "stage_history": [], "tool_log": []})
    identity = CallIdentity(_id("call"), lead, camp, "+919800000099", "R", client_id=client)
    async with AsyncSessionLocal() as db:
        db.add(CallRecord(call_id=identity.call_id, client_id=client, lead_id=lead, campaign_id=camp, caller_id_used="+911"))
        await db.commit()

    # Never answered: no charge
    await CallRecorder(identity).finalize(engine, 0.0)
    async with AsyncSessionLocal() as db:
        rec = await db.get(CallRecord, identity.call_id)
        assert rec.client_billed_seconds == 0 and rec.outcome == "unclear"
        assert (await db.get(Client, client)).balance_seconds == 600

    recorder = CallRecorder(identity)
    await recorder.mark_answered()
    await recorder.finalize(engine, 45.0)
    async with AsyncSessionLocal() as db:
        assert (await db.get(CallRecord, identity.call_id)).client_billed_seconds == 45
        assert (await db.get(Client, client)).balance_seconds == 600 - 45


async def test_credit_request_flow_and_double_approval():
    client = await make_client(minutes=0)
    http, admin = await signed_in("client", client), await signed_in("admin")
    assert (await http.get("/api/client/overview")).json()["minutes"]["out_of_minutes"] is True
    request_id = (await http.post("/api/client/credit-requests", json={"minutes": 500})).json()["id"]
    assert (await http.post(f"/api/admin/credit-requests/{request_id}/approve", json={})).status_code == 403
    assert (await admin.post(f"/api/admin/credit-requests/{request_id}/approve", json={})).json()["remaining_minutes"] == 500
    assert (await admin.post(f"/api/admin/credit-requests/{request_id}/approve", json={})).status_code == 409
    overview = (await http.get("/api/client/overview")).json()
    assert overview["minutes"]["remaining"] == 500 and overview["minutes"]["out_of_minutes"] is False


# ------------------------------------------------------------------------------
# Opt-outs
# ------------------------------------------------------------------------------
async def test_opt_out_blocks_only_that_client():
    a, b = await make_client(), await make_client()
    camp_a, camp_b = await make_campaign(a), await make_campaign(b)
    phone = "+919833333333"
    lead_a = await make_lead(a, camp_a, phone=phone)
    lead_b = await make_lead(b, camp_b, phone=phone)
    await record_opt_out(CallIdentity("", lead_a, camp_a, phone, "R", client_id=a))
    mgr = OutboundDialer().compliance_mgr
    allowed_a, _ = await mgr.can_dial_lead(phone, 0, a, skip_window_check=True)
    allowed_b, _ = await mgr.can_dial_lead(phone, 0, b, skip_window_check=True)
    assert allowed_a is False and allowed_b is True
    async with AsyncSessionLocal() as db:
        assert (await db.get(Lead, lead_a)).is_dnd is True
        assert (await db.get(Lead, lead_b)).is_dnd is False


async def test_number_that_is_not_a_lead_can_still_opt_out():
    client = await make_client()
    http = await signed_in("client", client)
    assert (await http.post("/api/client/opt-outs", json={"phone_number": "+919855555555"})).status_code == 200
    async with AsyncSessionLocal() as db:
        assert await db.get(DNDEntry, (client, "+919855555555")) is not None


async def test_national_dnd_blocks_every_client():
    client = await make_client()
    phone = "+919844444444"
    async with AsyncSessionLocal() as db:
        db.add(DNDEntry(client_id=GLOBAL_DND_CLIENT, phone_number=phone, source="dlt"))
        await db.commit()
    allowed, _ = await OutboundDialer().compliance_mgr.can_dial_lead(phone, 0, client, skip_window_check=True)
    assert allowed is False


# ------------------------------------------------------------------------------
# Dialer service
# ------------------------------------------------------------------------------
class FakeDialer(OutboundDialer):
    def __init__(self):
        super().__init__()
        self.dispatched = []

    async def dispatch(self, room, metadata):
        self.dispatched.append(metadata)
        return "dispatch"


@pytest.fixture
def open_window(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "COMPLIANCE_CALLING_START_HOUR", 0)
    monkeypatch.setattr(settings, "COMPLIANCE_CALLING_END_HOUR", 24)
    monkeypatch.setattr(settings, "PLIVO_MAX_CALLS_PER_SECOND", 1000)
    monkeypatch.setattr("campaign.dialer.settings.PLIVO_MAX_CALLS_PER_SECOND", 1000)


async def _clear_queue():
    from sqlalchemy import update

    async with AsyncSessionLocal() as db:
        await db.execute(update(DialJob).where(DialJob.status == "queued").values(status="cancelled"))
        await db.execute(update(CallRecord).where(CallRecord.status != "ended").values(status="ended"))
        await db.commit()


async def test_dialer_respects_client_cap_and_uses_client_number(open_window):
    await _clear_queue()
    client = await make_client(cap=2, caller_id="+911400000099")
    camp = await make_campaign(client)
    for _ in range(4):
        await make_lead(client, camp, queued=True)
    fake = FakeDialer()
    service = DialService(fake)
    assert await service.tick() == 2
    assert await service.tick() == 0  # still at its cap
    assert all(m["caller_id"] == "+911400000099" and m["client_id"] == client for m in fake.dispatched)


async def test_dialer_shares_capacity_across_clients(open_window, monkeypatch):
    await _clear_queue()
    settings = get_settings()
    a, b = await make_client(cap=50), await make_client(cap=50)
    camp_a, camp_b = await make_campaign(a), await make_campaign(b)
    for _ in range(10):
        await make_lead(a, camp_a, queued=True)
    await make_lead(b, camp_b, queued=True)
    monkeypatch.setattr(settings, "PLIVO_MAX_CALLS_PER_SECOND", 2)
    fake = FakeDialer()
    assert await DialService(fake).tick() == 2
    assert {m["client_id"] for m in fake.dispatched} == {a, b}  # the big client cannot starve the small one


async def test_dialer_waits_at_zero_minutes_and_resumes_after_top_up(open_window):
    await _clear_queue()
    client = await make_client(minutes=0)
    camp = await make_campaign(client)
    await make_lead(client, camp, queued=True)
    fake = FakeDialer()
    service = DialService(fake)
    assert await service.tick() == 0
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(DialJob).where(DialJob.client_id == client))).scalar()
        assert job.status == "queued"
        await add_minutes(db, client, 10, "top_up")
        await db.commit()
    assert await service.tick() == 1


async def test_dialer_skips_paused_campaigns_and_manual_callbacks(open_window):
    await _clear_queue()
    client = await make_client()
    paused = await make_campaign(client, status="paused")
    await make_lead(client, paused, queued=True)
    active = await make_campaign(client)
    lead = await make_lead(client, active)
    async with AsyncSessionLocal() as db:
        db.add(DialJob(client_id=client, lead_id=lead, campaign_id=active, status="manual", reason="human_expert_callback"))
        await db.commit()
    assert await DialService(FakeDialer()).tick() == 0


async def test_dialer_moves_jobs_outside_the_calling_window(monkeypatch):
    await _clear_queue()
    settings = get_settings()
    monkeypatch.setattr(settings, "COMPLIANCE_CALLING_START_HOUR", 0)
    monkeypatch.setattr(settings, "COMPLIANCE_CALLING_END_HOUR", 0)
    client = await make_client()
    camp = await make_campaign(client)
    await make_lead(client, camp, queued=True)
    fake = FakeDialer()
    assert await DialService(fake).tick() == 0
    assert fake.dispatched == []
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(DialJob).where(DialJob.client_id == client))).scalar()
        assert job.status == "queued" and job.attempt_number == 1


def test_outcome_labels():
    assert conclusion_for("payment_link_sent") == "ready"
    assert conclusion_for("opted_out") == "refused"
    assert conclusion_for("no_answer") == "contact_again"
    assert conclusion_for("manager_callback") == "manager"
    assert conclusion_for("unclear") == "unclear"
    assert conclusion_for("wrong_person") == "other"


# ------------------------------------------------------------------------------
# CRM webhook
# ------------------------------------------------------------------------------
async def test_webhook_is_signed_retried_and_skipped_without_a_url():
    client = await make_client()
    no_url = await make_client()
    async with AsyncSessionLocal() as db:
        (await db.get(Client, client)).crm_webhook_url = "https://crm.example.test/hook"
        (await db.get(Client, client)).crm_webhook_secret = "s3cret"
        first = CRMOutbox(client_id=client, event_type="call.interested", payload={"a": 1})
        other = CRMOutbox(client_id=no_url, event_type="call.interested", payload={"a": 2})
        db.add_all([first, other])
        await db.commit()
        first_id, other_id = first.id, other.id

    seen = []

    def failing(request):
        seen.append(request)
        return httpx.Response(500)

    def working(request):
        seen.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as http:
        assert await deliver_pending(http=http) == 0
    async with AsyncSessionLocal() as db:
        row = await db.get(CRMOutbox, first_id)
        assert row.status == "pending" and row.attempts == 1
        assert (await db.get(CRMOutbox, other_id)).status == "skipped"
        row.next_attempt_at = row.created_at
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.MockTransport(working)) as http:
        assert await deliver_pending(http=http) == 1
    assert seen[-1].headers["X-Signature-SHA256"] == sign(seen[-1].content, "s3cret")


# ------------------------------------------------------------------------------
# Knowledge base
# ------------------------------------------------------------------------------
def test_document_parsing():
    assert parse_document("a.txt", b"First para here.\n\nSecond para here.") == [
        ("First para here.", "First para here."), ("Second para here.", "Second para here.")]
    assert parse_document("a.csv", b"question,answer\nHours?,Open 9 to 5\n") == [("Hours?", "Open 9 to 5")]
    assert all(len(a) <= 500 for _, a in parse_document("a.md", ("Long sentence here. " * 100).encode()))
    with pytest.raises(DocumentError):
        parse_document("a.exe", b"x")
    with pytest.raises(DocumentError):
        parse_document("a.txt", b"  ")


async def test_knowledge_reaches_calls_only_after_approval():
    client = await make_client()
    camp = await make_campaign(client)
    http, admin = await signed_in("client", client), await signed_in("admin")
    files = {"file": ("faq.csv", b"question,answer\nWarranty?,The warranty lasts 18 months\n", "text/csv")}
    doc = (await http.post("/api/client/knowledge", files=files)).json()
    assert doc["status"] == "pending" and doc["chunks"] == 1
    assert (await load_knowledge(camp)).faqs == []
    assert (await http.post(f"/api/admin/knowledge/{doc['id']}/approve")).status_code == 403
    assert (await admin.post(f"/api/admin/knowledge/{doc['id']}/approve")).json()["status"] == "approved"

    knowledge = await load_knowledge(camp)
    assert len(knowledge.faqs) == 1
    auth = AuthorizationContext()
    tools = CallTools(CallIdentity("c", "l", camp, "+919", "R", client_id=client), knowledge, auth)
    found = await tools.execute("search_knowledge", {"query": "warranty kitni hai"})
    assert found["found"] and "18 months" in found["approved_text"][0] and 18.0 in auth.numbers
    assert (await tools.execute("search_knowledge", {"query": "zzz qqq"}))["found"] is False

    assert (await http.delete(f"/api/client/knowledge/{doc['id']}")).status_code == 200
    assert (await load_knowledge(camp)).faqs == []


async def test_payment_links_are_off_unless_enabled_for_the_client():
    client = await make_client()
    camp = await make_campaign(client)
    knowledge = await load_knowledge(camp)
    tools = CallTools(CallIdentity("c", "l", camp, "+919", "R", client_id=client), knowledge, AuthorizationContext())
    result = await tools.execute("send_payment_link", {"sku": "X", "channel": "whatsapp"})
    assert result["ok"] is False


async def test_new_client_setup_through_admin_api():
    admin = await signed_in("admin")
    created = (await admin.post("/api/admin/clients", json={"name": "Acme", "company_name": "Acme Ltd",
                                                            "initial_minutes": 30000})).json()
    client_id = created["id"]
    assert created["remaining_minutes"] == 30000 and created["max_concurrent_calls"] == 3
    login = (await admin.post(f"/api/admin/clients/{client_id}/users", json={"email": f"{_id('o')}@acme.test"})).json()
    assert len(login["temporary_password"]) >= 10
    content = {"products": [{"sku": "gold", "name": "Gold", "price_inr": 999}],
               "objection_playbooks": {"price": "Yeh sahi value hai sir."}}
    assert (await admin.put(f"/api/admin/clients/{client_id}/content", json=content)).status_code == 200

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    assert (await http.post("/api/auth/login", json={"email": login["email"], "password": login["temporary_password"]})).status_code == 200
    campaign = (await http.post("/api/client/campaigns", json={"name": "First", "product_skus": ["GOLD"]})).json()
    knowledge = await load_knowledge(campaign["id"])
    assert knowledge.company_name == "Acme Ltd" and knowledge.objection_playbooks["price"].startswith("Yeh")
    lead = {"phone_number": "+919877777777", "full_name": "Lead", "consent_source": "web form"}
    assert (await http.post(f"/api/client/campaigns/{campaign['id']}/leads", json={"leads": [lead]})).json()["imported_leads"] == 1
    started = (await http.post(f"/api/client/campaigns/{campaign['id']}/start")).json()
    assert started == {"status": "active", "queued": 1}
    assert (await http.post(f"/api/client/campaigns/{campaign['id']}/start")).json()["queued"] == 0  # no double queueing


async def test_one_product_with_several_plans_saves_its_delivery_time_once():
    admin = await signed_in("admin")
    client_id = (await admin.post("/api/admin/clients", json={"name": "Multi", "company_name": "Multi Ltd"})).json()["id"]
    eta = "10 minute mein online activation"
    content = {"products": [
        {"sku": "CRM-PRO", "plan": "Monthly", "name": "Pro", "price_inr": 999, "eta_text": eta},
        {"sku": "CRM-PRO", "plan": "Yearly", "name": "Pro", "price_inr": 9999, "billing_period": "yearly", "eta_text": eta},
        {"sku": "CRM-STARTER", "plan": "Monthly", "name": "Starter", "price_inr": 499, "eta_text": eta},
    ]}
    for _ in range(2):  # saving the same content again must also work
        assert (await admin.put(f"/api/admin/clients/{client_id}/content", json=content)).status_code == 200
    async with AsyncSessionLocal() as db:
        plans = (await db.execute(select(CatalogItem).where(CatalogItem.client_id == client_id,
                                                            CatalogItem.is_active.is_(True)))).scalars().all()
    assert sorted((p.sku, p.plan, p.price_inr) for p in plans) == [
        ("CRM-PRO", "Monthly", 999), ("CRM-PRO", "Yearly", 9999), ("CRM-STARTER", "Monthly", 499)]
    too_long = {"products": [{"sku": "X", "name": "X", "price_inr": 1, "eta_text": "x" * 129}]}
    reply = await admin.put(f"/api/admin/clients/{client_id}/content", json=too_long)
    assert reply.status_code == 422 and "eta_text" in str(reply.json()["detail"])


async def test_post_call_summary_is_stored_and_not_repeated():
    from offline.post_call_analyzer import PostCallAnalyzer
    from llm.client import Usage

    class FakeOffline:
        calls = 0

        async def complete(self, messages, **kw):
            FakeOffline.calls += 1
            return '{"summary": "Customer wants a demo.", "next_step": "Book it.", "sentiment": "positive"}', Usage()

    client = await make_client()
    camp = await make_campaign(client)
    lead = await make_lead(client, camp)
    call = await make_call(client, camp, lead, status="ended", outcome="interested", turn_count=3,
                           transcript=[{"speaker": "customer", "text": "demo dikhao"}])
    silent = await make_call(client, camp, lead, status="ended", outcome="no_answer", turn_count=0)
    analyzer = PostCallAnalyzer(FakeOffline())
    await analyzer.run_pending(limit=100)
    calls_after_first_run = FakeOffline.calls
    await analyzer.run_pending(limit=100)
    assert FakeOffline.calls == calls_after_first_run
    async with AsyncSessionLocal() as db:
        assert (await db.get(CallRecord, call)).summary["summary"] == "Customer wants a demo."
        assert (await db.get(CallRecord, silent)).summary is None


async def test_recording_is_off_by_default_and_failure_never_breaks_a_call(monkeypatch):
    from types import SimpleNamespace
    from observability.recording import signed_recording_url, start_recording

    settings = get_settings()
    requests = []

    async def fake_start(request):
        requests.append(request)

    lk = SimpleNamespace(egress=SimpleNamespace(start_room_composite_egress=fake_start))
    assert await start_recording(lk, "room", "c1", "call1") is None and requests == []

    monkeypatch.setattr(settings, "RECORDING_ENABLED", True)
    monkeypatch.setattr(settings, "RECORDING_S3_BUCKET", "bucket")
    monkeypatch.setattr(settings, "RECORDING_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(settings, "RECORDING_S3_SECRET_KEY", "secret")
    assert await start_recording(lk, "room", "c1", "call1") == "recordings/c1/call1.ogg"
    assert requests[0].audio_only is True and requests[0].room_name == "room"
    assert "recordings/c1/call1.ogg" in signed_recording_url("recordings/c1/call1.ogg")

    async def failing(request):
        raise RuntimeError("egress down")

    lk.egress.start_room_composite_egress = failing
    assert await start_recording(lk, "room", "c1", "call2") is None


# ------------------------------------------------------------------------------
# Calling a number from the portal, and the landing page demo
# ------------------------------------------------------------------------------
@pytest.fixture
def fake_portal_dialer(monkeypatch, open_window):
    from campaign import portal_api

    fake = FakeDialer()
    monkeypatch.setattr(portal_api, "_dialer", fake)
    return fake


def test_typed_numbers_are_normalized():
    from campaign.portal_api import normalize_phone

    for typed in ("+91 98765 43210", "98765-43210", "098765 43210", "(+91) 9876543210", "0091 9876543210"):
        assert normalize_phone(typed) == "+919876543210", typed
    assert normalize_phone("+1 415 555 0100") == "+14155550100"
    assert normalize_phone("12345") is None and normalize_phone("call me") is None


async def test_phone_call_waits_for_a_calling_provider(monkeypatch, fake_portal_dialer):
    monkeypatch.setattr(get_settings(), "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "")
    client = await make_client()
    campaign = await make_campaign(client)
    http = await signed_in("client", client)
    resp = await http.post("/api/client/phone-call", json={"campaign_id": campaign, "phone_number": "9876543210"})
    assert resp.status_code == 503 and "isn't set up yet" in resp.json()["detail"]
    assert fake_portal_dialer.dispatched == []


async def test_client_calls_a_typed_number_and_it_becomes_a_lead(monkeypatch, fake_portal_dialer):
    monkeypatch.setattr(get_settings(), "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "ST_test")
    await _clear_queue()
    client = await make_client(caller_id="+911400000077")
    campaign = await make_campaign(client)
    http = await signed_in("client", client)
    number = f"98{uuid.uuid4().int % 10**8:08d}"
    resp = await http.post("/api/client/phone-call",
                           json={"campaign_id": campaign, "phone_number": number, "customer_name": "Asha"})
    assert resp.status_code == 200 and resp.json()["status"] == "calling"
    sent = fake_portal_dialer.dispatched[-1]
    assert sent["phone_number"] == f"+91{number}" and sent["customer_name"] == "Asha" and sent["client_id"] == client
    async with AsyncSessionLocal() as db:
        lead = (await db.execute(select(Lead).where(Lead.client_id == client, Lead.phone_number == f"+91{number}"))).scalar()
    assert lead is not None and lead.campaign_id == campaign and "portal" in lead.consent_source

    bad = await http.post("/api/client/phone-call", json={"campaign_id": campaign, "phone_number": "12"})
    assert bad.status_code == 422
    other = await make_campaign(await make_client())
    assert (await http.post("/api/client/phone-call", json={"campaign_id": other, "phone_number": number})).status_code == 404


async def test_phone_call_explains_why_it_was_not_placed(monkeypatch, fake_portal_dialer):
    monkeypatch.setattr(get_settings(), "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "ST_test")
    client = await make_client(minutes=0, caller_id="+911400000077")
    campaign = await make_campaign(client)
    http = await signed_in("client", client)
    resp = await http.post("/api/client/phone-call", json={"campaign_id": campaign, "phone_number": "9876500000"})
    assert resp.status_code == 409 and "out of minutes" in resp.json()["detail"]


async def _demo(http, visitor):
    return await http.post("/api/demo/call", json={}, headers={"X-Forwarded-For": visitor})


async def test_demo_call_is_limited_per_visitor_and_time_boxed(monkeypatch, fake_portal_dialer):
    settings = get_settings()
    campaign = await make_campaign(await make_client())
    for key, value in {"DEMO_CALL_ENABLED": True, "DEMO_CAMPAIGN_ID": campaign, "DEMO_CALLS_PER_HOUR": 2,
                       "DEMO_MAX_CONCURRENT": 100, "DEMO_MAX_SECONDS": 90}.items():
        monkeypatch.setattr(settings, key, value)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")  # no login
    assert (await http.get("/api/demo")).json() == {"enabled": True, "max_seconds": 90}
    visitor = f"203.0.113.{uuid.uuid4().int % 250}-{uuid.uuid4().hex[:4]}"
    first = await _demo(http, visitor)
    assert first.status_code == 200 and first.json()["token"] and first.json()["max_seconds"] == 90
    sent = fake_portal_dialer.dispatched[-1]
    assert sent["test_call"] is True and sent["max_seconds"] == 90 and sent["campaign_id"] == campaign
    async with AsyncSessionLocal() as db:
        record = await db.get(CallRecord, first.json()["call_id"])
    assert record.direction == "demo"
    assert (await _demo(http, visitor)).status_code == 200
    assert (await _demo(http, visitor)).status_code == 429  # third in the hour
    assert (await _demo(http, visitor + "x")).status_code == 200  # another visitor is unaffected

    monkeypatch.setattr(settings, "DEMO_CALL_ENABLED", False)
    assert (await _demo(http, "198.51.100.1")).status_code == 404


async def test_demo_line_has_an_overall_cap(monkeypatch, fake_portal_dialer):
    settings = get_settings()
    await _clear_queue()
    campaign = await make_campaign(await make_client())
    for key, value in {"DEMO_CALL_ENABLED": True, "DEMO_CAMPAIGN_ID": campaign, "DEMO_CALLS_PER_HOUR": 100,
                       "DEMO_MAX_CONCURRENT": 1}.items():
        monkeypatch.setattr(settings, key, value)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    assert (await _demo(http, f"a-{uuid.uuid4().hex}")).status_code == 200
    busy = await _demo(http, f"b-{uuid.uuid4().hex}")
    assert busy.status_code == 429 and "busy" in busy.json()["detail"]
    await _clear_queue()  # the first demo call ended
    assert (await _demo(http, f"c-{uuid.uuid4().hex}")).status_code == 200


async def test_booked_demos_and_callbacks_show_as_appointments_for_their_client_only():
    client, other = await make_client(), await make_client()
    campaign = await make_campaign(client)
    lead = await make_lead(client, campaign)
    k = await load_knowledge(campaign)
    tools = CallTools(CallIdentity(_id("call"), lead, campaign, "+919", "R", client_id=client), k, AuthorizationContext())
    slots = await tools.execute("list_demo_slots", {})
    assert slots["slots"][0]["slot_id"].startswith("gen-")  # sales desk hours: no slot table for this client
    booked = await tools.execute("book_demo", {"slot_id": slots["slots"][0]["slot_id"]})
    assert booked["ok"] and "call ke baad details" in booked["approved_phrasing"]  # nothing is sent yet
    assert (await tools.execute("schedule_callback", {"when": "kal shaam 5 baje"}))["ok"]

    http = await signed_in("client", client)
    rows = (await http.get("/api/client/appointments")).json()["appointments"]
    assert {r["kind"] for r in rows} == {"demo", "callback"}
    assert rows == sorted(rows, key=lambda r: r["at"])
    demo = next(r for r in rows if r["kind"] == "demo")
    assert demo["lead_id"] == lead and demo["campaign_id"] == campaign and demo["duration_minutes"] == 15
    assert len((await http.get("/api/client/appointments", params={"kind": "demo"})).json()["appointments"]) == 1
    assert (await http.get("/api/client/appointments", params={"when": "past"})).json()["appointments"] == []
    csv_resp = await http.get("/api/client/appointments", params={"format": "csv"})
    assert csv_resp.headers["content-type"].startswith("text/csv") and "Demo" in csv_resp.text

    assert (await (await signed_in("client", other)).get("/api/client/appointments")).json()["appointments"] == []


async def test_campaign_language_is_chosen_changed_and_used_on_calls():
    from agent.dialogue import DialogueEngine
    from agent.prompts import build_messages
    from speech.phrase_cache import PreRenderedPhraseCache as P

    assert set(P.ENGLISH_TEXTS) == set(P.CANONICAL_TEXTS)  # every fixed line has an English version
    client = await make_client()
    await make_campaign(client)  # creates the client's product X
    http = await signed_in("client", client)
    made = (await http.post("/api/client/campaigns", json={"name": "En", "product_skus": ["X"], "language": "english"})).json()
    assert (await http.post("/api/client/campaigns", json={"name": "Bad", "product_skus": ["X"], "language": "tamil"})).status_code == 422
    listed = {c["id"]: c["language"] for c in (await http.get("/api/client/campaigns")).json()}
    assert listed[made["id"]] == "english"

    k = await load_knowledge(made["id"])
    engine = DialogueEngine(CallIdentity("c", "l", made["id"], "+919", "Rajesh", client_id=client), k, None)
    opening = engine.opening()
    assert opening[1].text == "Am I speaking with Rajesh?" and "This call is recorded" in opening[0].text
    system = build_messages(k, engine.stage, engine.state, "Rajesh", [])[0]["content"]
    assert "only natural, warm, simple Indian English" in system
    assert system.endswith("never copy the Hindi words.")  # last, so the Hinglish examples above are not copied
    engine.tools.price_allowed = True  # as after the customer asks
    price = await engine.tools.execute("get_price", {"sku": "X", "plan": "Monthly"})
    assert price["approved_phrasing"].endswith("costs ₹500 per month.")

    assert (await http.put(f"/api/client/campaigns/{made['id']}/language", json={"language": "hinglish"})).json() == {"language": "hinglish"}
    assert (await load_knowledge(made["id"])).language == "hinglish"
    assert (await http.put(f"/api/client/campaigns/{made['id']}/language", json={"language": "hindi"})).status_code == 422
    other = await signed_in("client", await make_client())
    assert (await other.put(f"/api/client/campaigns/{made['id']}/language", json={"language": "english"})).status_code == 404


async def test_landing_enquiry_is_validated_rate_limited_and_listed_for_admins():
    from database.models import Enquiry
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
                             headers={"x-forwarded-for": _id("ip")})
    body = {"person_name": "Asha", "phone_number": "98765 43210", "whatsapp_number": "098765-43210",
            "business_name": "Asha Stores", "business_type": "Retail"}
    assert (await http.post("/api/enquiries", json=body)).json() == {"status": "received"}
    assert (await http.post("/api/enquiries", json={**body, "phone_number": "abc"})).status_code == 422
    assert (await http.post("/api/enquiries", json={**body, "website": "spam.example"})).json() == {"status": "received"}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Enquiry).where(Enquiry.person_name == "Asha"))).scalars().all()
    assert len(rows) == 1 and rows[0].phone_number == "+919876543210" and rows[0].whatsapp_number == "+919876543210"
    for _ in range(get_settings().ENQUIRIES_PER_HOUR):
        last = await http.post("/api/enquiries", json=body)
    assert last.status_code == 429

    assert (await (await signed_in("client", await make_client())).get("/api/admin/enquiries")).status_code == 403
    admin = await signed_in("admin")
    listed = (await admin.get("/api/admin/enquiries", params={"status": "new"})).json()
    mine = next(e for e in listed if e["id"] == rows[0].id)
    assert mine["business_type"] == "Retail"
    assert (await admin.patch(f"/api/admin/enquiries/{mine['id']}", json={"status": "contacted"})).json() == {"status": "contacted"}
    assert (await http.get("/api/enquiries")).status_code == 405  # the public side can only send


async def test_trial_clients_test_in_the_browser_only_and_can_ask_about_pricing(monkeypatch, fake_portal_dialer):
    client = await make_client(minutes=100)
    async with AsyncSessionLocal() as db:
        (await db.get(Client, client)).is_trial = True
        await db.commit()
    campaign = await make_campaign(client, status="draft")
    http = await signed_in("client", client)
    overview = (await http.get("/api/client/overview")).json()
    assert overview["client"]["is_trial"]
    assert (await http.post(f"/api/client/campaigns/{campaign}/start")).status_code == 403
    assert (await http.post("/api/client/phone-call", json={"campaign_id": campaign, "phone_number": "9876543210"})).status_code == 403
    assert client not in await DialService()._eligible_clients()

    plans = (await http.get("/api/client/plans")).json()
    assert plans["is_trial"] and not plans["pricing_enquiry_open"]
    assert all("rate_per_minute_inr" not in p for p in plans["plans"])  # prices are discussed, not listed
    assert (await http.post("/api/client/enquiries", json={"plan_name": "P"})).json() == {"status": "received"}
    assert (await http.get("/api/client/plans")).json()["pricing_enquiry_open"]


async def test_logout_and_password_change_end_other_sessions():
    import asyncio as aio
    email, password = await make_user("client", await make_client())
    def browser():
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    laptop, phone = browser(), browser()
    for b in (laptop, phone):
        assert (await b.post("/api/auth/login", json={"email": email, "password": password})).status_code == 200
    await aio.sleep(1.1)  # session times are kept to the second
    new = "a-brand-new-password"
    assert (await laptop.post("/api/auth/password", json={"current_password": password, "new_password": new})).status_code == 200
    assert (await laptop.get("/api/auth/me")).status_code == 200  # this device gets a fresh session
    assert (await phone.get("/api/auth/me")).status_code == 401  # the other device is signed out

    await aio.sleep(1.1)
    assert (await phone.post("/api/auth/login", json={"email": email, "password": new})).status_code == 200
    await aio.sleep(1.1)
    await laptop.post("/api/auth/logout")
    assert (await phone.get("/api/auth/me")).status_code == 401  # logout signs out everywhere


async def test_security_headers_and_per_ip_login_limit(monkeypatch):
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
                             headers={"x-forwarded-for": _id("ip")})
    resp = await http.get("/api/demo")
    assert resp.headers["x-frame-options"] == "DENY" and resp.headers["x-content-type-options"] == "nosniff"
    monkeypatch.setattr(get_settings(), "LOGIN_MAX_FAILURES_PER_IP", 3)
    for i in range(3):  # three different emails from one address
        await http.post("/api/auth/login", json={"email": f"{_id('x')}@example.test", "password": "wrong"})
    email, password = await make_user("client", await make_client())
    assert (await http.post("/api/auth/login", json={"email": email, "password": password})).status_code == 429


async def test_self_signup_creates_a_trial_account_and_tells_the_team():
    from database.models import Enquiry
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
                             headers={"x-forwarded-for": _id("ip")})
    email = f"{_id('new')}@example.test"
    body = {"email": email, "password": "long-enough-pass", "phone_number": "98765 43210"}
    me = (await http.post("/api/auth/signup", json=body)).json()
    assert me["role"] == "client" and me["email"] == email
    overview = (await http.get("/api/client/overview")).json()  # signed in straight away
    assert overview["client"]["is_trial"] and overview["minutes"]["remaining"] == get_settings().TRIAL_TEST_MINUTES
    async with AsyncSessionLocal() as db:
        note = (await db.execute(select(Enquiry).where(Enquiry.client_id == me["client_id"]))).scalar()
    assert note.topic == "signup" and note.phone_number == "+919876543210"

    assert (await http.post("/api/auth/signup", json=body)).status_code == 409  # one account per email
    assert (await http.post("/api/auth/signup", json={**body, "email": "x@y.z", "password": "short"})).status_code == 422
    assert (await http.post("/api/auth/signup", json={**body, "email": "not-an-email"})).status_code == 422
    for i in range(get_settings().SIGNUPS_PER_DAY):
        last = await http.post("/api/auth/signup", json={**body, "email": f"{_id('n')}@example.test"})
    assert last.status_code == 429  # per address per day


async def test_trial_client_sets_up_its_own_agent_and_can_test_it():
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
                             headers={"x-forwarded-for": _id("ip")})
    me = (await http.post("/api/auth/signup", json={"email": f"{_id('t')}@example.test", "password": "long-enough-pass",
                                                    "phone_number": "9876543210"})).json()
    assert (await http.get("/api/client/setup")).json()["editable"] is True
    body = {"company_name": "Asha Stores", "about": "Hum kirana stores ko online laate hain.",
            "products": [{"name": "Shop Online", "price_inr": 999, "billing_period": "monthly",
                          "description": "Aapki dukaan ki website.", "features": ["Orders on WhatsApp", " "]}]}
    saved = (await http.put("/api/client/setup", json=body)).json()
    assert saved["campaign_created"]
    k = await load_knowledge(saved["campaign_created"])
    assert k.company_name == "Asha Stores" and k.company_intro.startswith("Hum kirana")
    assert [c.features for c in k.catalog.values()] == [["Orders on WhatsApp"]]
    assert (await http.get("/api/client/overview")).json()["client"]["name"] == "Asha Stores"
    again = (await http.put("/api/client/setup", json={**body, "about": ""})).json()
    assert again["campaign_created"] is None  # the campaign exists already
    assert not (await load_knowledge(saved["campaign_created"])).company_intro

    paid = await signed_in("client", await make_client())
    assert (await paid.put("/api/client/setup", json=body)).status_code == 403  # after the trial the team owns content


async def test_trial_client_fills_the_setup_form_from_a_document(monkeypatch):
    from llm.client import LLMClient, Usage
    seen = {}

    async def fake_complete(self, messages, **kwargs):
        seen["model"], seen["text"] = self.endpoints[0].model, messages[-1]["content"]
        return json.dumps({"company_name": "Asha Stores", "about": "Kirana stores online.", "products": [
            {"name": "Shop Online", "price_inr": 999, "billing_period": "annual", "features": ["Orders on WhatsApp", ""]},
            {"name": "Delivery", "price_inr": "cheap", "billing_period": "weekly"},  # unclear values are dropped
            {"price_inr": 5}]}), Usage()

    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test",
                             headers={"x-forwarded-for": _id("ip")})
    await http.post("/api/auth/signup", json={"email": f"{_id('d')}@example.test", "password": "long-enough-pass",
                                              "phone_number": "9876543210"})
    files = {"file": ("brochure.txt", "Asha Stores brings kirana shops online.".encode(), "text/plain")}
    draft = (await http.post("/api/client/setup/from-document", files=files)).json()
    assert seen == {"model": get_settings().OPENAI_MODEL, "text": "Asha Stores brings kirana shops online."}
    assert draft["company_name"] == "Asha Stores"
    assert draft["products"] == [
        {"name": "Shop Online", "price_inr": 999.0, "billing_period": "annual", "description": "",
         "features": ["Orders on WhatsApp"]},
        {"name": "Delivery", "price_inr": None, "billing_period": "monthly", "description": "", "features": []}]
    assert (await http.get("/api/client/setup")).json()["products"] == []  # a draft only; nothing saved

    bad = {"file": ("photo.png", b"\x89PNG", "image/png")}
    assert (await http.post("/api/client/setup/from-document", files=bad)).status_code == 400
    paid = await signed_in("client", await make_client())
    assert (await paid.post("/api/client/setup/from-document", files=files)).status_code == 403


async def test_trial_test_calls_spend_the_trial_minutes_but_paid_test_calls_are_free():
    from types import SimpleNamespace
    from llm.client import Usage

    engine = SimpleNamespace(summary=lambda: {
        "usage": Usage(), "outcome": None, "stage_reached": "PITCH", "disclosure_played": True, "turns": 2,
        "guard_replacements": 0, "transcript": [], "call_state": {}, "stage_history": [], "tool_log": []})
    balances = {}
    for trial in (True, False):
        client = await make_client(minutes=50)
        async with AsyncSessionLocal() as db:
            (await db.get(Client, client)).is_trial = trial
            await db.commit()
        camp = await make_campaign(client)
        lead = await make_lead(client, camp)
        identity = CallIdentity(_id("call"), lead, camp, "+919800000098", "R", client_id=client)
        async with AsyncSessionLocal() as db:
            db.add(CallRecord(call_id=identity.call_id, client_id=client, lead_id=lead, campaign_id=camp,
                              caller_id_used="browser", direction="test"))
            await db.commit()
        await CallRecorder(identity, console=True).finalize(engine, 60.0)
        async with AsyncSessionLocal() as db:
            balances[trial] = (await db.get(Client, client)).balance_seconds
    assert balances == {True: 50 * 60 - 60, False: 50 * 60}
