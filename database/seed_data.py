"""Seeds the database with the approved catalog, offers, policies, playbook, demo slots and a test lead.

    python -m database.seed_data            # create tables if missing, then upsert seed rows
    python -m database.seed_data --reset    # DROP all tables first (development only)
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from agent.knowledge import (
    SEED_AVAILABILITY,
    SEED_CAMPAIGN_ID,
    SEED_CATALOG,
    SEED_OBJECTIONS,
    SEED_POLICIES,
    SEED_SCRIPT_VERSION,
    SEED_STAGE_PROMPTS,
    seed_offers,
)
from config.settings import get_settings
from database.models import (
    Availability,
    Base,
    Campaign,
    CatalogItem,
    DemoSlot,
    Lead,
    Offer,
    Playbook,
    Policy,
    utcnow,
)
from database.session import AsyncSessionLocal, get_engine, init_db


async def reset_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))


async def seed_database(reset: bool = False) -> None:
    settings = get_settings()
    if reset:
        if settings.is_production:
            raise SystemExit("Refusing to reset a production database")
        await reset_schema()
    await init_db()

    now = utcnow()
    async with AsyncSessionLocal() as session:
        await session.merge(Campaign(
            id=SEED_CAMPAIGN_ID, name="Q4 Re-engagement", product_skus=["CRM-PRO", "CRM-STARTER"],
            script_version=SEED_SCRIPT_VERSION, is_promotional=True, status="draft",
            voice_id=settings.BULBUL_TTS_VOICE_ID,
        ))
        for c in SEED_CATALOG:
            await session.merge(CatalogItem(
                sku=c.sku, plan=c.plan, version=1, name=c.name, price_inr=c.price_inr,
                billing_period=c.billing_period, description=c.description, features=c.features, is_active=True,
            ))
        for o in seed_offers(now):
            await session.merge(Offer(
                id=o.id, campaign_id=SEED_CAMPAIGN_ID, sku=o.sku, plan=o.plan,
                discount_percentage=o.discount_percentage, discounted_price_inr=o.discounted_price_inr,
                valid_from=o.valid_from, valid_until=o.valid_until, max_uses=o.max_uses,
                current_uses=o.current_uses, per_lead_cap=o.per_lead_cap, is_active=True,
            ))
        for p in SEED_POLICIES:
            await session.merge(Policy(topic=p.topic, version=p.version, title=p.title,
                                       approved_text=p.approved_text, is_active=True))
        for a in SEED_AVAILABILITY:
            await session.merge(Availability(sku=a.sku, pincode_prefix=a.pincode_prefix,
                                             available=a.available, eta_text=a.eta_text))
        await session.merge(Playbook(
            script_version=SEED_SCRIPT_VERSION, campaign_id=SEED_CAMPAIGN_ID,
            stage_prompts=SEED_STAGE_PROMPTS, objection_playbooks=SEED_OBJECTIONS,
            status="approved", approved_by="seed", approved_at=now, is_active=True,
        ))
        await session.merge(Lead(
            id="LEAD-TEST-001", campaign_id=SEED_CAMPAIGN_ID, phone_number="+919876543210",
            full_name="Rajesh Sharma", preferred_language="hinglish", timezone="Asia/Kolkata",
            consent_source="Landing Page Webhook - 2026-11-03", consent_at=now,
            consent_notice_version=settings.CONSENT_NOTICE_VERSION, is_dnd=False, attempt_count=0,
        ))

        # Demo slots for the next 5 working days: 11:00, 15:00, 17:00 IST
        ist = timezone(timedelta(hours=5, minutes=30))
        today = datetime.now(ist).replace(minute=0, second=0, microsecond=0)
        existing = (await session.execute(text("SELECT count(*) FROM demo_slots"))).scalar()
        if not existing:
            for d in range(1, 8):
                day = today + timedelta(days=d)
                if day.weekday() == 6:
                    continue
                for hour in (11, 15, 17):
                    start = day.replace(hour=hour).astimezone(timezone.utc).replace(tzinfo=None)
                    session.add(DemoSlot(starts_at=start, duration_minutes=15, capacity=2))

        await session.commit()
    print("Database initialised and seeded.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop all tables first (development only)")
    asyncio.run(seed_database(reset=ap.parse_args().reset))
