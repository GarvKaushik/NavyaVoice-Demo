"""Lead import, campaign start and script publishing, shared by the operator API and the portal."""

import re
import time
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import Campaign, ConsentRecord, DialJob, DNDEntry, Lead, Playbook, utcnow

import uuid

PHONE_RE = re.compile(r"^\+\d{10,15}$")


async def import_leads(db: AsyncSession, client_id: str, campaign_id: str, items: Iterable[Any]) -> Dict[str, int]:
    """Adds leads to a campaign. Consent source is mandatory (DPDP Act) and recorded per lead.
    The same number can exist under different clients, but only once under each."""
    s = get_settings()
    imported, skipped, invalid = 0, 0, 0
    seen = set()
    for item in items:
        if not PHONE_RE.match(item.phone_number or "") or not (item.consent_source or "").strip():
            invalid += 1
            continue
        existing = (await db.execute(select(Lead.id).where(Lead.client_id == client_id,
                                                           Lead.phone_number == item.phone_number))).first()
        if existing or item.phone_number in seen:
            skipped += 1
            continue
        seen.add(item.phone_number)
        opted_out = await db.get(DNDEntry, (client_id, item.phone_number)) is not None
        lead_id = f"lead_{uuid.uuid4().hex[:10]}"
        db.add(Lead(id=lead_id, client_id=client_id, campaign_id=campaign_id, phone_number=item.phone_number,
                    full_name=item.full_name, preferred_language=item.preferred_language, timezone=item.timezone,
                    segment=item.segment, consent_source=item.consent_source, consent_at=utcnow(),
                    consent_notice_version=s.CONSENT_NOTICE_VERSION, is_dnd=opted_out,
                    status="opted_out" if opted_out else "pending"))
        await db.flush()
        db.add(ConsentRecord(client_id=client_id, lead_id=lead_id, phone_number=item.phone_number,
                             consent_source=item.consent_source, notice_version=s.CONSENT_NOTICE_VERSION,
                             event="consent_captured"))
        imported += 1
    return {"imported_leads": imported, "skipped_existing": skipped, "invalid": invalid}


async def start_campaign(db: AsyncSession, campaign: Campaign, batch_size: Optional[int] = None) -> int:
    """Activates the campaign and queues its eligible leads for the dialer service. Returns how many were queued."""
    s = get_settings()
    already_queued = select(DialJob.lead_id).where(DialJob.campaign_id == campaign.id, DialJob.status == "queued")
    q = select(Lead).where(Lead.client_id == campaign.client_id, Lead.campaign_id == campaign.id,
                           Lead.is_dnd.is_(False), Lead.status == "pending",
                           Lead.attempt_count < s.MAX_DIAL_ATTEMPTS_PER_LEAD, Lead.id.not_in(already_queued))
    if batch_size:
        q = q.limit(batch_size)
    leads = (await db.execute(q)).scalars().all()
    for lead in leads:
        db.add(DialJob(client_id=campaign.client_id, lead_id=lead.id, campaign_id=campaign.id, reason="initial",
                       attempt_number=(lead.attempt_count or 0) + 1))
    campaign.status = "active"
    return len(leads)


async def publish_playbook(db: AsyncSession, campaign: Campaign, stage_prompts: Dict[str, str],
                           objection_playbooks: Dict[str, str], approved_by: str) -> str:
    """Retires the campaign's approved script and publishes a new version; new calls pick it up."""
    rows = (await db.execute(select(Playbook).where(Playbook.campaign_id == campaign.id,
                                                    Playbook.status == "approved"))).scalars().all()
    for row in rows:
        row.status = "retired"
    version = f"v{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    db.add(Playbook(script_version=version, campaign_id=campaign.id, stage_prompts=stage_prompts,
                    objection_playbooks=objection_playbooks, status="approved", approved_by=approved_by,
                    approved_at=utcnow(), created_by=approved_by))
    campaign.script_version = version
    return version
