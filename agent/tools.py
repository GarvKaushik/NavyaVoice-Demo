"""Deterministic Server-Validated Tools for the Voice Sales Agent.

Every price, offer, date and commitment the agent speaks must come from one of
these tools. Identifiers (lead, campaign, call) are injected from the call
context and are never taken from the LLM. Each result is registered in the
call's AuthorizationContext so the OutputGuard allows exactly those values.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytz

from agent.callback_time import _WORD_NUM, _num, parse_callback_time, speak_time
from agent.knowledge import CampaignKnowledge
from config.settings import get_settings
from database.models import DEFAULT_CLIENT_ID
from guardrails.authorization import AuthorizationContext
from observability import metrics as m
from services.redis_service import get_redis_service

logger = logging.getLogger(__name__)


@dataclass
class CallIdentity:
    call_id: str
    lead_id: str
    campaign_id: str
    phone_number: str
    customer_name: str
    segment: Optional[str] = None
    timezone: str = "Asia/Kolkata"
    client_id: str = DEFAULT_CLIENT_ID


@dataclass
class PendingAction:
    """An action the transport must perform after the current reply finishes playing."""
    kind: str  # end_call | transfer
    outcome: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


# A completed action the customer has been told about; nothing later in the call may overwrite it
CONFIRMED_OUTCOMES = {"demo_booked", "payment_link_sent"}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "get_price",
        "description": "Active catalog price and approved phrasing for a product plan. Call before saying any price.",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string", "description": "Product SKU from MAIN PRODUCT or OTHER PLANS"},
            "plan": {"type": "string", "description": "Monthly or Annual"}},
            "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "get_offer",
        "description": "Eligible promotional offer for this customer, or none. Call ONLY when the customer asks about "
                       "an offer/discount or says the price is high.",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"}, "plan": {"type": "string"}}, "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "check_availability",
        "description": "Activation time estimate. Call ONLY when the customer asks how soon they can start using it.",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"}, "pincode": {"type": "string"}}, "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "get_policy",
        "description": "Approved policy text (refunds, cancellation, data_residency). Use it word for word.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "enum": ["refunds", "cancellation", "data_residency"]}},
            "required": ["topic"]}}},
    {"type": "function", "function": {
        "name": "list_demo_slots",
        "description": "Next available live demo slots. Call before proposing any demo time.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "book_demo",
        "description": "Book a demo slot the customer agreed to. Only after the customer chose a slot.",
        "parameters": {"type": "object", "properties": {
            "slot_id": {"type": "string", "description": "slot_id from list_demo_slots"}},
            "required": ["slot_id"]}}},
    {"type": "function", "function": {
        "name": "send_payment_link",
        "description": "Send a payment link on WhatsApp or SMS after the customer agreed to buy. Price is computed server-side.",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"}, "plan": {"type": "string"},
            "channel": {"type": "string", "enum": ["whatsapp", "sms"]}},
            "required": ["sku", "channel"]}}},
    {"type": "function", "function": {
        "name": "transfer_to_human",
        "description": "Warm transfer to a human sales expert when the customer asks or the question is beyond scope.",
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string"}}, "required": ["reason"]}}},
    {"type": "function", "function": {
        "name": "schedule_callback",
        "description": "Schedule a callback at the time the customer asked for, e.g. 'kal shaam 5 baje'.",
        "parameters": {"type": "object", "properties": {
            "when": {"type": "string", "description": "The customer's words for the time"}},
            "required": ["when"]}}},
    {"type": "function", "function": {
        "name": "record_opt_out",
        "description": "Customer does not want any more calls. Writes the DND list immediately.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "end_call",
        "description": "End the call politely when the customer is not interested or the conversation is complete.",
        "parameters": {"type": "object", "properties": {
            "outcome": {"type": "string", "enum": ["not_interested", "interested", "completed", "wrong_person"]}},
            "required": ["outcome"]}}},
]


SEARCH_KNOWLEDGE_SCHEMA: Dict[str, Any] = {"type": "function", "function": {
    "name": "search_knowledge",
    "description": "Approved answers for questions no other tool covers. Use only what it returns.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "The customer's question, in a few words"}},
        "required": ["query"]}}}


def tool_schemas_for(knowledge: CampaignKnowledge) -> List[Dict[str, Any]]:
    """The knowledge tool is sent only to campaigns that have approved documents."""
    # Payment links stay in code but are not offered on calls yet
    tools = [t for t in TOOL_SCHEMAS if t["function"]["name"] != "send_payment_link"]
    return tools + [SEARCH_KNOWLEDGE_SCHEMA] if knowledge.faqs else tools


def _words(text: str) -> set:
    return {w for w in re.findall(r"\w+", (text or "").lower()) if len(w) > 2}


class CallTools:
    """Server-side tools accessible by the real-time LLM during a call."""

    def __init__(self, identity: CallIdentity, knowledge: CampaignKnowledge, auth: AuthorizationContext):
        self.identity = identity
        self.knowledge = knowledge
        self.auth = auth
        self.redis = get_redis_service()
        self.settings = get_settings()
        self.pending_action: Optional[PendingAction] = None
        self.outcome: Optional[str] = None
        self.last_confirmation: Optional[str] = None  # approved line for the last completed action
        self.price_allowed: bool = True  # the dialogue engine holds this until the customer asks
        self.link_consented: bool = True  # the dialogue engine sets these two from each customer turn
        self.opt_out_allowed: bool = True
        self.end_call_allowed: bool = True  # off while the agent still has a question to ask on this turn
        self.amount_heard: bool = True  # the dialogue engine holds this until a price or offer is given
        self.offered_slot_phrases: Dict[str, str] = {}  # the slots last offered, kept for later turns
        self.tool_log: List[Dict[str, Any]] = []
        self.demo_at: Optional[datetime] = None  # booked demo time, sent to the CRM
        self.callback_at: Optional[datetime] = None
        self._offered_slots: Dict[str, datetime] = {}
        self._presented_offers: Dict[tuple, Any] = {}  # offers the customer has actually heard on this call
        self._links_sent: set = set()  # a plan's link goes out once per call

    # ------------------------------------------------------------------
    async def execute(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            result = {"ok": False, "error": f"unknown tool {name}"}
        else:
            try:
                result = await handler(**{k: v for k, v in arguments.items() if v is not None})
            except TypeError as e:
                result = {"ok": False, "error": f"bad arguments: {e}"}
            except Exception as e:  # tools must never crash the call
                logger.exception(f"Tool {name} failed")
                m.record_vendor_error("tool", name)
                result = {"ok": False, "error": "internal error, offer a callback or human expert"}
        self.tool_log.append({"tool": name, "arguments": arguments, "result": result,
                              "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()})
        return result

    def _approve(self, text: str) -> str:
        self.auth.authorize_text(text)
        return text

    @property
    def english(self) -> bool:
        return self.knowledge.language == "english"

    def _say(self, hinglish: str, english: str) -> str:
        """The approved line in the campaign's language (Hinglish unless the campaign is English)."""
        return self._approve(english if self.english else hinglish)

    def _when(self, moment: datetime) -> str:
        return speak_time(moment, english=self.english)

    def _resolve_plan(self, sku: str, plan: Optional[str]) -> Optional[tuple]:
        sku = (sku or "").upper().strip()
        plan = (plan or "Monthly").strip().capitalize()
        if (sku, plan) in self.knowledge.catalog:
            return sku, plan
        return None

    # ------------------------------------------------------------------
    async def tool_get_price(self, sku: str, plan: str = "Monthly") -> Dict[str, Any]:
        if not self.price_allowed:
            return {"ok": False, "error": "the customer has not asked the price; do not mention it, continue the conversation"}
        key = self._resolve_plan(sku, plan)
        if key is None:
            options = [f"{s}/{p}" for s, p in self.knowledge.catalog]
            return {"ok": False, "error": "unknown product or plan", "available": options}
        item = self.knowledge.catalog[key]
        price = f"₹{int(item.price_inr):,}"
        per = {"monthly": "per month", "annual": "per year", "yearly": "per year"}.get(item.billing_period)
        if per:
            phrasing = self._say(f"{item.name} ka price {price} {per} hai.", f"{item.name} costs {price} {per}.")
        else:  # one-time and anything else: never claim a period the catalog does not state
            phrasing = self._say(f"{item.name} ka price {price} hai, ek baar ka.", f"{item.name} costs {price}, one-time.")
        self.amount_heard = True
        self.auth.authorize_numbers([item.price_inr])
        return {"ok": True, "sku": item.sku, "plan": item.plan, "name": item.name, "price_inr": item.price_inr,
                "billing_period": item.billing_period, "features": item.features, "approved_phrasing": phrasing}

    def _eligible_offer(self, sku: str, plan: str):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for o in self.knowledge.offers:
            if o.sku != sku or o.plan != plan:
                continue
            if o.valid_from and o.valid_from > now or o.valid_until <= now:
                continue
            if o.customer_id and o.customer_id != self.identity.lead_id:
                continue
            if o.eligible_segments and self.identity.segment not in o.eligible_segments:
                continue
            if o.max_uses and o.current_uses >= o.max_uses:
                continue
            return o
        return None

    async def tool_get_offer(self, sku: str, plan: str = "Monthly") -> Dict[str, Any]:
        key = self._resolve_plan(sku, plan)
        if key is None:
            return {"ok": False, "error": "unknown product or plan"}
        offer = await self._offer_for_lead(*key)
        if offer is None:
            return {"ok": True, "offer": None, "approved_phrasing": self._say(
                "Abhi is plan par koi special offer nahi hai.", "There is no special offer on this plan right now.")}
        self._presented_offers[key] = offer
        self.amount_heard = True
        base = self.knowledge.catalog[key].price_inr
        self.auth.authorize_numbers([offer.discount_percentage, offer.discounted_price_inr, base])
        self.auth.authorize_terms(["discount", "offer"])
        phrasing = self._say(
            f"Special offer mein aapko {int(offer.discount_percentage)}% discount mil raha hai, "
            f"yaani ₹{int(base):,} ki jagah sirf ₹{int(offer.discounted_price_inr):,}.",
            f"With the special offer you get {int(offer.discount_percentage)}% off, "
            f"so just ₹{int(offer.discounted_price_inr):,} instead of ₹{int(base):,}.")
        return {"ok": True, "offer": {"offer_id": offer.id, "discount_percentage": offer.discount_percentage,
                                      "discounted_price_inr": offer.discounted_price_inr,
                                      "original_price_inr": base},
                "approved_phrasing": phrasing}

    async def _offer_for_lead(self, sku: str, plan: str):
        """The one eligibility check used by both get_offer and send_payment_link."""
        offer = self._eligible_offer(sku, plan)
        if offer is None or await self._lead_offer_uses(offer.id) >= offer.per_lead_cap:
            return None
        return offer

    async def _lead_offer_uses(self, offer_id: str) -> int:
        """Redemptions by this lead (paid links; an unpaid link does not use up the offer)."""
        try:
            from sqlalchemy import func, select
            from database.models import PaymentLink
            from database.session import get_read_sessionmaker

            async with get_read_sessionmaker()() as db:
                q = select(func.count()).select_from(PaymentLink).where(
                    PaymentLink.lead_id == self.identity.lead_id, PaymentLink.offer_id == offer_id,
                    PaymentLink.status == "paid")
                return int((await db.execute(q)).scalar() or 0)
        except Exception:
            return 0

    async def tool_check_availability(self, sku: str, pincode: str = "") -> Dict[str, Any]:
        sku = (sku or "").upper().strip()
        pincode = "".join(ch for ch in str(pincode) if ch.isdigit())
        matches = [a for a in self.knowledge.availability
                   if a.sku == sku and (a.pincode_prefix == "" or pincode.startswith(a.pincode_prefix))]
        if not matches:
            return {"ok": False, "error": "no availability data; offer a human expert"}
        best = max(matches, key=lambda a: len(a.pincode_prefix))
        if not best.available:
            return {"ok": True, "available": False, "approved_phrasing": self._say("Abhi aapke area mein yeh available nahi hai.",
                                                                                 "This is not available in your area yet.")}
        self.auth.mark_action("check_availability")
        return {"ok": True, "available": True, "approved_phrasing": self._say(f"Aapke liye {best.eta_text} ho jayega.",
                                                                                f"For you: {best.eta_text}.")}

    async def tool_get_policy(self, topic: str) -> Dict[str, Any]:
        policy = self.knowledge.policies.get((topic or "").lower().strip())
        if policy is None:
            return {"ok": False, "error": "no approved policy on this topic", "topics": list(self.knowledge.policies)}
        self._approve(policy.approved_text)
        self.auth.mark_action(f"policy:{policy.topic}")
        return {"ok": True, "topic": policy.topic, "approved_text": policy.approved_text}

    async def tool_search_knowledge(self, query: str) -> Dict[str, Any]:
        wanted = _words(query)
        scored = [(len(wanted & _words(f"{f['question']} {f['answer']}")), f) for f in self.knowledge.faqs]
        best = [f for score, f in sorted(scored, key=lambda x: -x[0])[:2] if score > 0]
        if not best:
            return {"ok": True, "found": False, "say": "You do not have this detail. Offer a senior expert or a callback."}
        for f in best:
            self._approve(f["answer"])
        self.auth.mark_action("search_knowledge")
        return {"ok": True, "found": True, "approved_text": [f["answer"] for f in best]}

    # ------------------------------------------------------------------
    async def tool_list_demo_slots(self) -> Dict[str, Any]:
        tz = pytz.timezone(self.identity.timezone or self.settings.TIMEZONE)
        slots = await self._db_demo_slots(tz) or self._generated_demo_slots(tz)
        out = []
        self.offered_slot_phrases = {}
        for slot_id, start in slots[:3]:
            self._offered_slots[slot_id] = start
            phrase = self._when(start)
            self.offered_slot_phrases[slot_id] = phrase
            out.append({"slot_id": slot_id, "spoken": phrase})
            self._approve(phrase + " " + start.strftime("%d %B %A"))
            self.auth.authorize_numbers([start.hour % 12 or 12, start.day])
        self.auth.authorize_numbers([15])
        offered = [s["spoken"] for s in out[:2]]
        if not offered:
            return {"ok": False, "error": "no demo slots; offer a callback instead"}
        if len(offered) == 1:
            phrase = self._say(f"Mere paas {offered[0]} ka slot hai. Kya yeh aapke liye theek rahega?",
                               f"I have {offered[0]}. Does that work for you?")
        else:
            phrase = self._say(f"Mere paas {offered[0]} ya {offered[1]} ka slot hai. Aapke liye kaunsa theek rahega?",
                               f"I have {offered[0]} or {offered[1]}. Which one works for you?")
        return {"ok": True, "duration_minutes": 15, "slots": out, "approved_phrasing": phrase}

    async def _db_demo_slots(self, tz) -> List[tuple]:
        try:
            from sqlalchemy import select
            from database.models import DemoSlot
            from database.session import get_read_sessionmaker

            async with get_read_sessionmaker()() as db:
                rows = (await db.execute(select(DemoSlot).where(
                    DemoSlot.client_id == self.identity.client_id, DemoSlot.is_active.is_(True), DemoSlot.starts_at > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
                    DemoSlot.booked_count < DemoSlot.capacity).order_by(DemoSlot.starts_at).limit(3))).scalars().all()
            return [(str(r.id), pytz.utc.localize(r.starts_at).astimezone(tz)) for r in rows]
        except Exception:
            return []

    def _generated_demo_slots(self, tz) -> List[tuple]:
        """Sales-desk hours when no slot table is configured."""
        now = datetime.now(tz)
        slots, day = [], now
        while len(slots) < 3:
            for hour in (11, 15, 17):
                start = day.replace(hour=hour, minute=0, second=0, microsecond=0)
                if start > now + timedelta(hours=2) and start.weekday() < 6:
                    slots.append((f"gen-{start.strftime('%Y%m%d%H')}", start))
                if len(slots) == 3:
                    break
            day = day + timedelta(days=1)
        return slots

    def _resolve_slot(self, value: Any) -> Optional[str]:
        """The offered slot the model means, whether it sends "3", "slot_id 3", "slot_3" or the spoken time."""
        text = str(value).strip()
        if text in self._offered_slots:
            return text
        bare = re.sub(r"^(slot[_\s-]*(id)?[\s:=_-]*)", "", text, flags=re.IGNORECASE).strip()
        if bare in self._offered_slots:
            return bare
        return next((slot_id for slot_id, spoken in self.offered_slot_phrases.items() if spoken.lower() == text.lower()), None)

    def slot_from_words(self, text: str) -> Optional[str]:
        """The one offered slot the customer's words point to ("gyarah baje wala", "shaam wala", "pehla"), or None."""
        t = text.lower()
        ids = list(self.offered_slot_phrases)
        for index, words in enumerate((("pehla", "pehle", "first"), ("doosra", "dusra", "second"))):
            if index < len(ids) and any(re.search(rf"\b{w}\b", t) for w in words):
                return ids[index]
        numbers = "|".join(_WORD_NUM)
        hour = re.search(rf"\b(\d{{1,2}}|{numbers})\s*(baje|bje|wala|wali|o'?clock)\b", t)
        matches = [(slot_id, self._offered_slots[slot_id]) for slot_id in ids]
        if hour and _num(hour.group(1)) is not None:
            matches = [m for m in matches if m[1].hour % 12 == _num(hour.group(1)) % 12]
        part = next((p for p in ("subah", "dopahar", "shaam") if re.search(rf"\b{p}\b", t)), None)
        if part:
            matches = [m for m in matches if {"subah": m[1].hour < 12, "dopahar": 12 <= m[1].hour < 16,
                                              "shaam": m[1].hour >= 16}[part]]
        return matches[0][0] if (hour or part) and len(matches) == 1 else None

    async def tool_book_demo(self, slot_id: str) -> Dict[str, Any]:
        slot_id = self._resolve_slot(slot_id)
        if slot_id is None:
            return {"ok": False, "error": "unknown slot_id; use a slot_id from DEMO SLOTS YOU OFFERED or call list_demo_slots"}
        start = self._offered_slots[slot_id]
        key = f"demo:{self.identity.call_id}:{slot_id}"
        booked = await self._db_book_slot(slot_id, key, start)
        if booked is False:
            return {"ok": False, "error": "slot just got full; offer another slot"}
        self.auth.mark_action("book_demo")
        self.outcome = "demo_booked"
        self.demo_at = start
        # Nothing is sent yet (WhatsApp/SMS sending is planned), so the line only says details will follow
        phrase = self._say(f"Aapka demo {speak_time(start)} ke liye book ho gaya hai. "
                           "Is call ke baad details aapko WhatsApp ya SMS par bhej di jayengi.",
                           f"Your demo is booked for {self._when(start)}. "
                           "After this call, you will get the details on WhatsApp or SMS.")
        self.last_confirmation = phrase
        return {"ok": True, "booking_id": key, "approved_phrasing": phrase,
                "next": "Say this line and thank the customer. Do not ask any more questions."}

    async def _db_book_slot(self, slot_id: str, key: str, start: datetime) -> Optional[bool]:
        """True booked, False full, None when the booking could not be saved. Generated (sales desk hours)
        slots have no capacity row, so they are saved without one."""
        generated = slot_id.startswith("gen-")
        try:
            from sqlalchemy import select, update
            from database.models import DemoBooking, DemoSlot
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                existing = (await db.execute(select(DemoBooking).where(DemoBooking.idempotency_key == key))).scalar()
                if existing:
                    return True
                duration = 15
                if not generated:
                    res = await db.execute(update(DemoSlot).where(
                        DemoSlot.id == int(slot_id), DemoSlot.booked_count < DemoSlot.capacity
                    ).values(booked_count=DemoSlot.booked_count + 1))
                    if res.rowcount == 0:
                        return False
                    slot = await db.get(DemoSlot, int(slot_id))
                    duration = slot.duration_minutes if slot and slot.duration_minutes else duration
                db.add(DemoBooking(idempotency_key=key, client_id=self.identity.client_id,
                                   campaign_id=self.identity.campaign_id, slot_id=None if generated else int(slot_id),
                                   lead_id=self.identity.lead_id, call_id=self.identity.call_id,
                                   starts_at=start.astimezone(pytz.utc).replace(tzinfo=None), duration_minutes=duration))
                await db.commit()
                return True
        except Exception as e:
            logger.warning(f"Demo booking DB write failed: {e!r}")
            return None

    async def tool_send_payment_link(self, sku: str, plan: str = "Monthly", channel: str = "whatsapp") -> Dict[str, Any]:
        if not self.knowledge.payment_links_enabled:
            return {"ok": False, "error": "payment links are not available; offer a human expert or callback"}
        key = self._resolve_plan(sku, plan)
        if key is None:
            return {"ok": False, "error": "unknown product or plan"}
        if key in self._links_sent:
            return {"ok": True, "already_sent": True, "approved_phrasing": self.last_confirmation}
        if not self.link_consented:
            return {"ok": False, "error": "the customer has not agreed to a payment link; do not send it. "
                                          "If it fits, ask: kya main payment link bhej doon?"}
        if not self.amount_heard:
            return {"ok": False, "error": "the customer has not heard the amount; call get_price and say it first"}
        channel = "sms" if str(channel).lower() == "sms" else "whatsapp"
        # Price recomputed server-side; never trusted from the LLM. The offer price applies only if the
        # customer heard that offer on this call and it is still valid, so the link matches what was said.
        item = self.knowledge.catalog[key]
        offer = self._presented_offers.get(key)
        if offer is not None and await self._offer_for_lead(*key) is None:
            offer = None
        amount = offer.discounted_price_inr if offer else item.price_inr
        idempotency_key = f"pay:{self.identity.call_id}:{key[0]}:{key[1]}"

        from integrations.payments import send_payment_link

        result = await send_payment_link(
            idempotency_key=idempotency_key, lead_id=self.identity.lead_id, call_id=self.identity.call_id,
            phone=self.identity.phone_number, customer_name=self.identity.customer_name,
            sku=item.sku, plan=item.plan, product_name=item.name, amount_inr=amount,
            offer_id=offer.id if offer else None, channel=channel,
        )
        if not result.get("ok"):
            return {"ok": False, "error": "payment link could not be sent; offer a human expert or callback"}
        self.auth.mark_action("send_payment_link")
        self.auth.authorize_numbers([amount])
        self.outcome = "payment_link_sent"
        self._links_sent.add(key)
        where = "WhatsApp" if channel == "whatsapp" else "SMS"
        phrase = self._approve(f"Maine ₹{int(amount):,} ka payment link aapke {where} par bhej diya hai.")
        self.last_confirmation = phrase
        return {"ok": True, "amount_inr": amount, "channel": channel, "approved_phrasing": phrase,
                "sandbox": result.get("sandbox", False)}

    async def tool_transfer_to_human(self, reason: str = "customer_request") -> Dict[str, Any]:
        tz = pytz.timezone(self.settings.TIMEZONE)
        hour = datetime.now(tz).hour
        numbers = self.knowledge.transfer_numbers
        staffed = self.settings.HUMAN_TRANSFER_START_HOUR <= hour < self.settings.HUMAN_TRANSFER_END_HOUR
        free = await self.redis.pick_free_human_agent(numbers) if numbers and staffed else None
        if not free:
            when = parse_callback_time("kal subah", tz_name=self.identity.timezone)
            await self._schedule_callback(when, "human_expert_callback")
            self.auth.mark_action("schedule_callback")
            if self.outcome not in CONFIRMED_OUTCOMES:
                self.outcome = "manager_callback"
            phrase = self._say(f"Abhi hamare experts available nahi hain. Main {speak_time(when)} team ke saath aapki "
                               "meeting fix kar deti hoon. Is call ke baad details aapko WhatsApp ya SMS par bhej di jayengi.",
                               f"Our experts are not available right now, so I am setting up a meeting with the team "
                               f"{self._when(when)}. After this call, you will get the details on WhatsApp or SMS.")
            return {"ok": False, "transferred": False, "approved_phrasing": phrase}
        self.auth.mark_action("transfer_to_human")
        self.outcome = "transferred"
        self.pending_action = PendingAction("transfer", "transferred", {"to": free, "reason": reason})
        return {"ok": True, "transferred": True,
                "approved_phrasing": self._say("Main aapki call hamare senior expert ko transfer kar rahi hoon, line par bane rahiye.",
                                               "I am transferring your call to our senior expert, please stay on the line.")}

    async def tool_schedule_callback(self, when: str) -> Dict[str, Any]:
        moment = parse_callback_time(when, tz_name=self.identity.timezone)
        if moment is None:
            return {"ok": False, "error": "time not understood; ask the customer for a day and time"}
        await self._schedule_callback(moment, "callback")
        self.auth.mark_action("schedule_callback")
        if self.outcome not in CONFIRMED_OUTCOMES:
            self.outcome = "callback_scheduled"
        phrase = self._say(f"Theek hai, main aapko {speak_time(moment)} call karti hoon.",
                           f"Sure, I will call you {self._when(moment)}.")
        self.pending_action = PendingAction("end_call", self.outcome, {"callback_at": moment.isoformat()})
        return {"ok": True, "callback_at": moment.isoformat(), "approved_phrasing": phrase}

    async def _schedule_callback(self, moment: datetime, reason: str) -> None:
        self.callback_at = moment
        try:
            from database.models import DialJob
            from database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                # An expert returns this one by hand; the AI dialer must not pick it up
                db.add(DialJob(client_id=self.identity.client_id, lead_id=self.identity.lead_id,
                               campaign_id=self.identity.campaign_id, reason=reason,
                               status="manual" if reason == "human_expert_callback" else "queued",
                               scheduled_at=moment.astimezone(pytz.utc).replace(tzinfo=None)))
                await db.commit()
        except Exception as e:
            logger.warning(f"Callback job not persisted ({e!r}); recorded on call only")

    async def tool_record_opt_out(self) -> Dict[str, Any]:
        if not self.opt_out_allowed:
            return {"ok": False, "error": "a short 'nahi' only answers your question and is not an opt-out; continue the call"}
        await record_opt_out(self.identity, channel="voice_tool")
        self.auth.mark_action("record_opt_out")
        self.outcome = "opted_out"
        self.pending_action = PendingAction("end_call", "opted_out")
        return {"ok": True, "approved_phrasing": self._say("Maine aapka number calling list se hata diya hai.",
                                                           "I have removed your number from our calling list.")}

    async def tool_end_call(self, outcome: str = "completed") -> Dict[str, Any]:
        if not self.end_call_allowed:
            return {"ok": False, "error": "do not end the call yet; ask your question and wait for the answer"}
        self.outcome = self.outcome or outcome
        self.pending_action = PendingAction("end_call", self.outcome)
        return {"ok": True, "say": "Short polite goodbye in one sentence."}


async def record_opt_out(identity: CallIdentity, channel: str = "voice") -> None:
    """Writes the client's opt-out to Redis (all dialers see it immediately) and Postgres (permanent)."""
    redis = get_redis_service()
    await redis.mark_opted_out(identity.phone_number, identity.client_id, reason=channel)
    m.record_opt_out()
    try:
        from sqlalchemy import update
        from database.models import ConsentRecord, DNDEntry, DialJob, Lead, utcnow
        from database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await db.merge(DNDEntry(client_id=identity.client_id, phone_number=identity.phone_number, source="opt_out",
                                    reason=channel, call_id=identity.call_id))
            await db.execute(update(Lead).where(Lead.client_id == identity.client_id,
                                                Lead.phone_number == identity.phone_number)
                             .values(is_dnd=True, dnd_source="opt_out", status="opted_out"))
            await db.execute(update(DialJob).where(DialJob.lead_id == identity.lead_id, DialJob.status == "queued")
                             .values(status="cancelled", last_error="opted_out"))
            if await db.get(Lead, identity.lead_id) is not None:  # a number can opt out before it is ever a lead
                db.add(ConsentRecord(client_id=identity.client_id, lead_id=identity.lead_id,
                                     phone_number=identity.phone_number, call_id=identity.call_id, event="opt_out",
                                     consent_source="voice", opted_out=True, opt_out_timestamp=utcnow(),
                                     opt_out_channel=channel))
            await db.commit()
    except Exception as e:
        logger.error(f"Opt-out Postgres write failed for {identity.phone_number}: {e!r} (Redis entry is permanent)")
    logger.warning(f"CONSENT WITHDRAWN & DND RECORDED: lead={identity.lead_id} phone={identity.phone_number}")
