"""Delivers queued call outcomes to each client's CRM, signed and with retries (transactional outbox)."""

import hashlib
import hmac
import json
import logging
from datetime import timedelta
from typing import Optional

import httpx
from sqlalchemy import select

from config.settings import get_settings
from database.models import Client, CRMOutbox, DEFAULT_CLIENT_ID, utcnow
from database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8


def sign(body: bytes, secret: str) -> str:
    return hmac.new((secret or "").encode(), body, hashlib.sha256).hexdigest()


async def deliver_pending(limit: int = 50, http: Optional[httpx.AsyncClient] = None) -> int:
    """Sends due events. Returns how many were delivered. Events of a client with no webhook are closed."""
    s = get_settings()
    delivered = 0
    client = http or httpx.AsyncClient(timeout=10.0)
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(CRMOutbox).where(
                CRMOutbox.status == "pending", CRMOutbox.next_attempt_at <= utcnow()
            ).order_by(CRMOutbox.id).limit(limit).with_for_update(skip_locked=True))).scalars().all()
            for row in rows:
                owner = await db.get(Client, row.client_id)
                url = owner.crm_webhook_url if owner else None
                secret = owner.crm_webhook_secret if owner else None
                if not url and row.client_id == DEFAULT_CLIENT_ID:
                    url, secret = s.CRM_WEBHOOK_URL, s.CRM_WEBHOOK_SECRET
                if not url:
                    row.status = "skipped"
                    continue
                body = json.dumps({"event": row.event_type, "data": row.payload}, ensure_ascii=False).encode()
                row.attempts = (row.attempts or 0) + 1
                try:
                    resp = await client.post(url, content=body, headers={
                        "Content-Type": "application/json", "X-Signature-SHA256": sign(body, secret)})
                    resp.raise_for_status()
                    row.status, row.delivered_at, row.last_error = "delivered", utcnow(), None
                    delivered += 1
                except Exception as e:
                    row.last_error = repr(e)[:500]
                    if row.attempts >= MAX_ATTEMPTS:
                        row.status = "dead"
                        logger.error(f"CRM event {row.id} dead after {row.attempts} attempts: {e!r}")
                    else:
                        row.next_attempt_at = utcnow() + timedelta(minutes=2 ** row.attempts)
            await db.commit()
    finally:
        if http is None:
            await client.aclose()
    return delivered
