"""Campaign knowledge snapshot loaded into worker memory at call start.

Holds the campaign's catalog slice, offers, policies, availability and the
approved playbook. Tools read from this snapshot, so exact values are
deterministic and nothing on the per-turn path touches the database.

Loads from Postgres when available; otherwise falls back to the approved
seed content below (the same content `database/seed_data.py` writes).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


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
SEED_SCRIPT_VERSION = "v1.0-hinglish-sales"

SEED_PRODUCT_INTRO = (
    "Nexus CRM Pro ek AI calling aur CRM software hai: yeh aapki team ke leads ko khud call karta hai, "
    "har call ki details apne aap CRM mein update karta hai, aur follow-up yaad dilata hai."
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
        "Disclosure ho chuka hai. Confirm karein ki sahi vyakti se baat ho rahi hai. Phir ek line mein batayein ki "
        "aap kis product ke liye call kar rahi hain (PRODUCT INTRO se, apne shabdon mein) aur 2 minute ki permission "
        "maangein. Permission na mile to politely call khatam karein ya callback ka samay poochein."
    ),
    "DISCOVERY": (
        "Customer ki situation samjhein: abhi sales leads ko kaise call karte hain, kaun sa tool use karte hain, "
        "aur manual calling mein kya pareshani aati hai. Ek baar mein sirf ek sawaal."
    ),
    "NEED_IDENTIFICATION": (
        "Customer ki main need ko ek chhote sentence mein wapas confirm karein aur poochein ki kya yeh sahi hai."
    ),
    "PITCH": (
        "Pehle product ka naam lekar ek simple line mein batayein ki yeh kya hai (PRODUCT INTRO). Phir sirf 1-2 "
        "features jo confirm ki gayi need ko solve karein. Price tabhi batayein jab customer pooche, aur hamesha "
        "get_price tool se. Offer ya discount ka zikr tabhi karein jab customer pooche ya price ko mehenga bole."
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
    "price": (
        "Main samajh sakti hoon sir. Lekin hamara standard Pro plan sirf ₹1,499 per month hai, "
        "aur aaj special offer mein yeh aapko sirf ₹1,199 mein mil jayega."
    ),
    "timing": (
        "Koi baat nahi sir, main samajh sakti hoon aap busy hain. Aapko kab call karna theek rahega?"
    ),
    "trust": (
        "Bilkul genuine concern hai sir. Hamare servers Mumbai mein hain, data India mein hi rehta hai, "
        "aur hamare paas 7-day money-back guarantee hai bina kisi lock-in ke."
    ),
    "competitor": (
        "Aapka current software accha hai sir, lekin hum specialized Hinglish voice calling "
        "aur automatic CRM entry dete hain, jo manual kaam bachata hai."
    ),
    "need": (
        "Bilkul sir, agar aapki team daily 20 se zyada calls karti hai, toh hamara agent "
        "unka kaafi samay bacha sakta hai. Kya main ek chhota demo dikha doon?"
    ),
}


def seed_offers(now: Optional[datetime] = None) -> List[OfferEntry]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return [OfferEntry("OFFER-DIWALI-20", "CRM-PRO", "Monthly", 20.0, 1199.0,
                       valid_until=now + timedelta(days=30), valid_from=now - timedelta(days=1),
                       max_uses=500, current_uses=12, per_lead_cap=1)]


def seed_knowledge(campaign_id: str = SEED_CAMPAIGN_ID) -> CampaignKnowledge:
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
    )


async def load_knowledge(campaign_id: str) -> CampaignKnowledge:
    """Loads the campaign slice from Postgres; falls back to seed content if unavailable."""
    try:
        from sqlalchemy import select
        from database.session import get_read_sessionmaker
        from database.models import Availability, Campaign, CatalogItem, Offer, Playbook, Policy, utcnow

        now = utcnow()
        async with get_read_sessionmaker()() as db:
            campaign = await db.get(Campaign, campaign_id)
            skus = list(campaign.product_skus) if campaign and campaign.product_skus else None

            q = select(CatalogItem).where(CatalogItem.is_active.is_(True))
            if skus:
                q = q.where(CatalogItem.sku.in_(skus))
            items = (await db.execute(q.order_by(CatalogItem.version))).scalars().all()
            catalog = {(i.sku, i.plan): CatalogEntry(i.sku, i.plan, i.name, i.price_inr, i.billing_period,
                                                      i.description or "", list(i.features or []))
                       for i in items}  # later versions overwrite earlier

            offers = (await db.execute(select(Offer).where(
                Offer.is_active.is_(True), Offer.valid_until > now,
                (Offer.campaign_id == campaign_id) | (Offer.campaign_id.is_(None))))).scalars().all()

            policies = (await db.execute(select(Policy).where(Policy.is_active.is_(True)))).scalars().all()
            availability = (await db.execute(select(Availability))).scalars().all()

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
            source="database",
            primary_sku=skus[0] if skus else None,
        )
    except Exception as e:
        logger.warning(f"Knowledge load from database failed ({e!r}); using approved seed content")
        return seed_knowledge(campaign_id)
