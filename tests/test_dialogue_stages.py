"""Tests for stage transitions, server tools and unit cost accounting."""

from datetime import date

from agent.knowledge import seed_knowledge
from agent.stages import CallStep, SalesStage, StageManager, extract_signals
from agent.tools import CallIdentity, CallTools
from guardrails.authorization import AuthorizationContext
from config.settings import get_settings
from observability.metrics import CostCalculator


def _apply(sm, text):
    return sm.apply(extract_signals(text))


def test_stage_flow_and_backward_moves():
    sm = StageManager()
    assert sm.endpointing_delay_ms == 300
    _apply(sm, "haan Rajesh speaking")
    assert sm.current_stage == SalesStage.OPENING and sm.step == CallStep.HOW_ARE_YOU
    _apply(sm, "main theek hoon")
    assert sm.current_stage == SalesStage.OPENING and sm.step == CallStep.OFFER
    sm.note_agent_reply("Main NavyaNexus se bol rahi hoon. Kya aap aisa software lena chahenge?")
    _apply(sm, "haan kya hai yeh")
    assert sm.current_stage == SalesStage.PITCH
    _apply(sm, "thoda mehenga hai")
    assert sm.current_stage == SalesStage.OBJECTION_HANDLING
    _apply(sm, "phir bhi mehenga hai yaar")
    assert sm.current_stage == SalesStage.CLOSE_NEXT_STEP  # second objection on same topic -> next step


def test_price_follows_one_explanation_when_nothing_more_is_asked():
    sm = StageManager(SalesStage.PITCH)
    sm.note_agent_reply("Yeh leads ko khud call karta hai.")
    sig = extract_signals("achha theek hai")
    sm.apply(sig)
    assert sig.wants_price and sm.current_stage == SalesStage.CLOSE_NEXT_STEP


def test_a_question_gets_a_second_explanation_then_the_price():
    sm = StageManager(SalesStage.PITCH)
    sm.note_agent_reply("Yeh leads ko khud call karta hai.")
    sig = extract_signals("aur kya kya karta hai")
    sm.apply(sig)
    assert not sig.wants_price and sm.current_stage == SalesStage.PITCH
    sm.note_agent_reply("Yeh follow-up reminders bhi deta hai.")
    sig = extract_signals("aur kya kya karta hai")
    sm.apply(sig)
    assert sig.wants_price and sm.current_stage == SalesStage.CLOSE_NEXT_STEP


def test_a_no_does_not_get_the_price():
    sm = StageManager(SalesStage.PITCH)
    sm.note_agent_reply("Yeh leads ko khud call karta hai.")
    sig = extract_signals("nahi")
    sm.apply(sig)
    assert not sig.wants_price and sm.current_stage == SalesStage.PITCH


def test_small_talk_bridging():
    sm = StageManager()
    sm.record_turn(is_small_talk=True)
    assert sm.should_bridge_back() is False
    sm.record_turn(is_small_talk=True)
    assert sm.should_bridge_back() is True


async def test_tools_price_offer_and_unknown_product():
    auth = AuthorizationContext()
    tools = CallTools(CallIdentity("c1", "LEAD-TEST-001", "CAMP-REENGAGE-Q4", "+919000000001", "Test"),
                      seed_knowledge(), auth)
    assert 1499.0 not in auth.numbers
    price = await tools.execute("get_price", {"sku": "CRM-PRO", "plan": "Monthly"})
    assert price["price_inr"] == 1499.0 and 1499.0 in auth.numbers
    offer = await tools.execute("get_offer", {"sku": "CRM-PRO", "plan": "Monthly"})
    assert offer["offer"]["discounted_price_inr"] == 1199.0 and 1199.0 in auth.numbers and "discount" in auth.terms
    unknown = await tools.execute("get_price", {"sku": "FAKE-SKU"})
    assert unknown["ok"] is False  # never a default price


async def test_payment_link_charges_what_the_customer_heard():
    # No offer presented on the call -> list price
    auth = AuthorizationContext()
    tools = CallTools(CallIdentity("c2", "LEAD-TEST-001", "CAMP-REENGAGE-Q4", "+919000000002", "Test"),
                      seed_knowledge(), auth)
    first = await tools.execute("send_payment_link", {"sku": "CRM-PRO", "plan": "Monthly", "channel": "whatsapp"})
    second = await tools.execute("send_payment_link", {"sku": "CRM-PRO", "plan": "Monthly", "channel": "whatsapp"})
    assert first["ok"] and first["amount_inr"] == 1499.0
    assert second["ok"] and "send_payment_link" in auth.completed_actions  # idempotent resend

    # Offer presented on the call -> offer price, recomputed server-side
    auth2 = AuthorizationContext()
    tools2 = CallTools(CallIdentity("c3", "LEAD-TEST-001", "CAMP-REENGAGE-Q4", "+919000000003", "Test"),
                       seed_knowledge(), auth2)
    await tools2.execute("get_offer", {"sku": "CRM-PRO", "plan": "Monthly"})
    link = await tools2.execute("send_payment_link", {"sku": "CRM-PRO", "plan": "Monthly", "channel": "whatsapp"})
    assert link["amount_inr"] == 1199.0


def test_cost_calculator_pulse_accounting(monkeypatch):
    monkeypatch.setattr(get_settings(), "TTS_PROVIDER", "sarvam")  # local speech has no per-character cost
    cost = CostCalculator.compute_call_cost(duration_seconds=65, stt_audio_seconds=40.0, tts_character_count=350,
                                            llm_input_tokens=12000, llm_cached_tokens=8000, llm_output_tokens=300,
                                            on=date(2026, 12, 1))
    assert cost.billed_minutes == 2  # 60 s pulse
    assert cost.telephony_inr == round(2 * 0.45, 4)
    assert cost.tts_inr == round(350 / 10000 * 20, 4)  # standard rate after the promotion
    assert cost.total_inr > cost.telephony_inr
