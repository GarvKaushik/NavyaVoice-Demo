"""Campaign knowledge snapshot loaded into worker memory at call start.

Holds the campaign's catalog slice, offers, policies, availability and the
approved playbook. Tools read from this snapshot, so exact values are
deterministic and nothing on the per-turn path touches the database.

Loads from Postgres. Outside production, the seed campaign falls back to the
approved seed content below (the same content `database/seed_data.py` writes).
No other campaign ever does: a client's lead must never hear another product's pitch.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config.settings import get_settings
from database.models import DEFAULT_CLIENT_ID

logger = logging.getLogger(__name__)


class KnowledgeUnavailable(RuntimeError):
    """This campaign's approved content could not be loaded, so it must not be called."""


@dataclass
class CatalogEntry:
    sku: str
    plan: str
    name: str
    price_inr: float
    billing_period: str
    description: str = ""
    features: List[str] = field(default_factory=list)


@dataclass
class OfferEntry:
    id: str
    sku: str
    plan: str
    discount_percentage: float
    discounted_price_inr: float
    valid_until: datetime
    valid_from: Optional[datetime] = None
    eligible_segments: Optional[List[str]] = None
    customer_id: Optional[str] = None
    max_uses: int = 1
    current_uses: int = 0
    per_lead_cap: int = 1


@dataclass
class PolicyEntry:
    topic: str
    title: str
    approved_text: str
    version: str = "v1.0"


@dataclass
class AvailabilityEntry:
    sku: str
    pincode_prefix: str
    available: bool
    eta_text: str


@dataclass
class CampaignKnowledge:
    campaign_id: str
    script_version: str
    catalog: Dict[tuple, CatalogEntry]
    offers: List[OfferEntry]
    policies: Dict[str, PolicyEntry]
    availability: List[AvailabilityEntry]
    stage_prompts: Dict[str, str]
    objection_playbooks: Dict[str, str]
    faqs: List[Dict[str, str]] = field(default_factory=list)
    source: str = "seed"
    primary_sku: Optional[str] = None  # first SKU in the campaign's product list
    client_id: str = DEFAULT_CLIENT_ID
    company_name: str = ""
    persona_name: str = ""
    complaint_contact: str = ""
    voice_id: str = ""
    caller_id: Optional[str] = None
    transfer_numbers: List[str] = field(default_factory=list)
    payment_links_enabled: bool = True

    def product_names(self) -> List[str]:
        return sorted({f"{c.name} ({c.sku}, {c.plan})" for c in self.catalog.values()})

    def primary_product(self) -> Optional[CatalogEntry]:
        """The plan the campaign pitches by default: the campaign's first SKU, monthly plan preferred."""
        entries = list(self.catalog.values())
        if self.primary_sku:
            entries = [c for c in entries if c.sku == self.primary_sku] or entries
        return next((c for c in entries if c.billing_period == "monthly"), entries[0] if entries else None)


# ------------------------------------------------------------------------------
# Approved seed content
# ------------------------------------------------------------------------------
SEED_CAMPAIGN_ID = "CAMP-REENGAGE-Q4"
SEED_SCRIPT_VERSION = "v1.1-hinglish-sales"

SEED_PRODUCT_INTRO = (
    "Nexus CRM Pro ek AI calling aur CRM software hai: yeh aapke leads ko khud call karta hai "
    "aur har call ki details CRM mein update karta hai."
)

SEED_CATALOG = [
    CatalogEntry("CRM-PRO", "Monthly", "Nexus CRM Pro", 1499.0, "monthly", SEED_PRODUCT_INTRO,
                 ["automatic Hinglish calling", "CRM mein seedhi entry", "lead follow-up reminders", "call analytics"]),
    CatalogEntry("CRM-PRO", "Annual", "Nexus CRM Pro Annual", 14990.0, "annual",
                 "CRM Pro ka saalana plan, priority onboarding ke saath",
                 ["automatic Hinglish calling", "CRM mein seedhi entry", "priority onboarding"]),
    CatalogEntry("CRM-STARTER", "Monthly", "Nexus CRM Starter", 799.0, "monthly",
                 "Akele sales rep ke liye entry-level AI dialer",
                 ["automatic calling", "basic CRM"]),
]

SEED_POLICIES = [
    PolicyEntry("refunds", "7-Day Money-Back Guarantee",
                "Agar aap pehle 7 dinon mein santusht nahi hote hain, toh aapko poora 100% refund mil jayega."),
    PolicyEntry("cancellation", "Hassle-Free Cancellation",
                "Aap dashboard se kabhi bhi ek click mein apna subscription cancel kar sakte hain. Koi lock-in period nahi hai."),
    PolicyEntry("data_residency", "Indian Data Residency",
                "Aapka poora customer data aur recordings Mumbai data center mein encrypted rehte hain, DPDP Act ke hisaab se."),
]

SEED_AVAILABILITY = [
    AvailabilityEntry("CRM-PRO", "", True, "10 minute mein online activation"),
    AvailabilityEntry("CRM-STARTER", "", True, "10 minute mein online activation"),
]

SEED_STAGE_PROMPTS: Dict[str, str] = {
    "OPENING": (
        "Disclosure ho chuka hai. Confirm karein ki sahi vyakti se baat ho rahi hai, haal-chaal poochein, phir ek line "
        "mein batayein ki company kya karti hai aur poochein ki kya unhe aisa product chahiye."
    ),
    "DISCOVERY": (
        "Customer ki situation samjhein: abhi sales leads ko kaise call karte hain, kaun sa tool use karte hain, "
        "aur manual calling mein kya pareshani aati hai. Ek baar mein sirf ek sawaal."
    ),
    "NEED_IDENTIFICATION": (
        "Customer ki main need ko ek chhote sentence mein wapas confirm karein aur poochein ki kya yeh sahi hai."
    ),
    "PITCH": (
        "Product ka naam lekar ek simple line mein batayein ki yeh kya hai aur customer ke kaam ka kaise hai (PRODUCT INTRO). "
        "Agli baar koi nayi baat batayein, wahi line mat dohrayein. Price khud mat batayein. "
        "Offer ya discount ka zikr tabhi karein jab customer pooche ya price ko mehenga bole."
    ),
    "OBJECTION_HANDLING": (
        "Objection ko respect karein aur neeche diya approved answer use karein. Kabhi argue mat karein."
    ),
    "CLOSE_NEXT_STEP": (
        "Clear next step offer karein: WhatsApp par payment link, 15-minute ka live demo, ya expert se baat. "
        "Action hamesha tool se karein aur tool ka result aane ke baad hi confirm karein."
    ),
}

SEED_OBJECTIONS: Dict[str, str] = {
    "price": "Samajh sakti hoon sir. Aaj offer mein yeh sirf ₹1,199 hai, kya link bhej doon?",
    "timing": "Koi baat nahi sir. Aapko kab call karna theek rahega?",
    "trust": "Bilkul sir. Data India mein hi rehta hai, aur 7-day money-back guarantee hai, koi lock-in nahi.",
    "competitor": "Aapka software accha hai sir. Hum Hinglish calling aur automatic CRM entry bhi dete hain.",
    "need": "Daily 20 se zyada calls par yeh kaafi samay bachata hai sir. Chhota demo dikha doon?",
}


# Starting script for a new client's campaigns; an admin adds product details and objection answers.
DEFAULT_STAGE_PROMPTS: Dict[str, str] = {
    "OPENING": SEED_STAGE_PROMPTS["OPENING"],
    "DISCOVERY": (
        "Customer ki situation samjhein: abhi yeh kaam kaise karte hain aur kya pareshani aati hai. "
        "Ek baar mein sirf ek sawaal."
    ),
    "NEED_IDENTIFICATION": SEED_STAGE_PROMPTS["NEED_IDENTIFICATION"],
    "PITCH": SEED_STAGE_PROMPTS["PITCH"],
    "OBJECTION_HANDLING": SEED_STAGE_PROMPTS["OBJECTION_HANDLING"],
    "CLOSE_NEXT_STEP": SEED_STAGE_PROMPTS["CLOSE_NEXT_STEP"],
}


def seed_offers(now: Optional[datetime] = None) -> List[OfferEntry]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return [OfferEntry("OFFER-DIWALI-20", "CRM-PRO", "Monthly", 20.0, 1199.0,
                       valid_until=now + timedelta(days=30), valid_from=now - timedelta(days=1),
                       max_uses=500, current_uses=12, per_lead_cap=1)]


def seed_knowledge(campaign_id: str = SEED_CAMPAIGN_ID) -> CampaignKnowledge:
    s = get_settings()
    return CampaignKnowledge(
        campaign_id=campaign_id,
        script_version=SEED_SCRIPT_VERSION,
        catalog={(c.sku, c.plan): c for c in SEED_CATALOG},
        offers=seed_offers(),
        policies={p.topic: p for p in SEED_POLICIES},
        availability=list(SEED_AVAILABILITY),
        stage_prompts=dict(SEED_STAGE_PROMPTS),
        objection_playbooks=dict(SEED_OBJECTIONS),
        source="seed",
        primary_sku=SEED_CATALOG[0].sku,
        company_name=s.COMPANY_NAME,
        persona_name=s.AGENT_PERSONA_NAME,
        complaint_contact=s.COMPLAINT_CONTACT,
        voice_id=s.tts_voice,
        transfer_numbers=s.human_transfer_numbers,
    )


async def load_knowledge(campaign_id: str) -> CampaignKnowledge:
    """Loads the campaign slice and its client's identity from Postgres."""
    s = get_settings()
    try:
        from sqlalchemy import select
        from database.session import get_read_sessionmaker
        from database.models import (Availability, Campaign, CatalogItem, Client, FAQ, Offer, Playbook, Policy,
                                     utcnow)

        now = utcnow()
        async with get_read_sessionmaker()() as db:
            campaign = await db.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(f"Unknown campaign {campaign_id}")
            client = await db.get(Client, campaign.client_id)
            if client is None:
                raise LookupError(f"Campaign {campaign_id} has no client")
            skus = list(campaign.product_skus) if campaign.product_skus else None

            q = select(CatalogItem).where(CatalogItem.client_id == client.id, CatalogItem.is_active.is_(True))
            if skus:
                q = q.where(CatalogItem.sku.in_(skus))
            items = (await db.execute(q.order_by(CatalogItem.version))).scalars().all()
            catalog = {(i.sku, i.plan): CatalogEntry(i.sku, i.plan, i.name, i.price_inr, i.billing_period,
                                                      i.description or "", list(i.features or []))
                       for i in items}  # later versions overwrite earlier

            offers = (await db.execute(select(Offer).where(
                Offer.client_id == client.id, Offer.is_active.is_(True), Offer.valid_until > now,
                (Offer.campaign_id == campaign_id) | (Offer.campaign_id.is_(None))))).scalars().all()

            policies = (await db.execute(select(Policy).where(Policy.client_id == client.id,
                                                              Policy.is_active.is_(True)))).scalars().all()
            availability = (await db.execute(select(Availability).where(Availability.client_id == client.id))).scalars().all()
            faqs = (await db.execute(select(FAQ).where(
                FAQ.client_id == client.id, FAQ.is_active.is_(True),
                (FAQ.campaign_id == campaign_id) | (FAQ.campaign_id.is_(None))).limit(500))).scalars().all()

            pq = select(Playbook).where(Playbook.campaign_id == campaign_id, Playbook.status == "approved")
            if campaign and campaign.script_version:
                pq = pq.where(Playbook.script_version == campaign.script_version)
            playbook = (await db.execute(pq.order_by(Playbook.approved_at.desc()))).scalars().first()

        if not catalog or playbook is None:
            raise LookupError(f"No active catalog or approved playbook for {campaign_id}")

        return CampaignKnowledge(
            campaign_id=campaign_id,
            script_version=playbook.script_version,
            catalog=catalog,
            offers=[OfferEntry(o.id, o.sku, o.plan, o.discount_percentage, o.discounted_price_inr, o.valid_until,
                               o.valid_from, o.eligible_segments, o.customer_id, o.max_uses or 1,
                               o.current_uses or 0, o.per_lead_cap or 1) for o in offers],
            policies={p.topic: PolicyEntry(p.topic, p.title, p.approved_text, p.version) for p in policies},
            availability=[AvailabilityEntry(a.sku, a.pincode_prefix, a.available, a.eta_text) for a in availability],
            stage_prompts=dict(playbook.stage_prompts),
            objection_playbooks=dict(playbook.objection_playbooks),
            faqs=[{"question": f.question, "answer": f.answer} for f in faqs],
            source="database",
            primary_sku=skus[0] if skus else None,
            client_id=client.id,
            company_name=client.company_name,
            persona_name=client.agent_persona_name or s.AGENT_PERSONA_NAME,
            complaint_contact=client.complaint_contact or s.COMPLAINT_CONTACT,
            # Stored voices are Bulbul names, so they apply only when Bulbul is speaking
            voice_id=(s.tts_voice if s.TTS_PROVIDER != "sarvam" else campaign.voice_id or client.voice_id or s.tts_voice),
            caller_id=client.caller_id,
            transfer_numbers=list(client.transfer_numbers or []),
            payment_links_enabled=bool(client.payment_links_enabled),
        )
    except Exception as e:
        if s.is_production or campaign_id != SEED_CAMPAIGN_ID:
            raise KnowledgeUnavailable(f"No approved content for campaign {campaign_id}: {e!r}") from e
        logger.warning(f"Knowledge load from database failed ({e!r}); using approved seed content")
        return seed_knowledge(campaign_id)
