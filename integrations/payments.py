"""Payment links (Razorpay) delivered over WhatsApp or SMS (Plivo).

Idempotent: the same idempotency key never creates or sends a second link.
Without Razorpay keys outside production, a sandbox link is logged instead
(nothing is sent to the customer) so the conversation can be tested end to end.
"""

import logging
import uuid
from typing import Any, Dict, Optional

import httpx

from config.settings import get_settings
from integrations.messaging import send_message
from observability import metrics as m

logger = logging.getLogger(__name__)
_sent_in_process: Dict[str, Dict[str, Any]] = {}


async def _existing(idempotency_key: str) -> Optional[Dict[str, Any]]:
    if idempotency_key in _sent_in_process:
        return _sent_in_process[idempotency_key]
    try:
        from sqlalchemy import select
        from database.models import PaymentLink
        from database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(PaymentLink).where(PaymentLink.idempotency_key == idempotency_key))).scalar()
            if row and row.status in ("sent", "paid"):
                return {"ok": True, "url": row.url, "amount_inr": row.amount_inr, "duplicate": True}
    except Exception:
        pass
    return None


async def _persist(**fields) -> None:
    try:
        from database.models import PaymentLink
        from database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            db.add(PaymentLink(**fields))
            await db.commit()
    except Exception as e:
        logger.warning(f"Payment link not persisted: {e!r}")


async def _create_razorpay_link(idempotency_key: str, amount_inr: float, name: str, phone: str, description: str) -> Dict[str, Any]:
    s = get_settings()
    body = {
        "amount": int(round(amount_inr * 100)),
        "currency": "INR",
        "reference_id": idempotency_key[:40],  # Razorpay rejects a reused reference_id
        "description": description[:200],
        "customer": {"name": name, "contact": phone},
        "notify": {"sms": False, "email": False},
        "expire_by": None,
    }
    import time

    body["expire_by"] = int(time.time()) + s.PAYMENT_LINK_EXPIRY_HOURS * 3600
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post("https://api.razorpay.com/v1/payment_links", json=body,
                                 auth=(s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET))
        resp.raise_for_status()
        data = resp.json()
    return {"id": data["id"], "url": data["short_url"]}


async def send_payment_link(
    *, idempotency_key: str, lead_id: str, call_id: str, phone: str, customer_name: str,
    sku: str, plan: str, product_name: str, amount_inr: float, offer_id: Optional[str], channel: str,
) -> Dict[str, Any]:
    existing = await _existing(idempotency_key)
    if existing:
        return existing

    s = get_settings()
    sandbox = not (s.RAZORPAY_KEY_ID and s.RAZORPAY_KEY_SECRET)
    if sandbox and s.is_production:
        logger.error("Razorpay keys missing in production; payment link not sent")
        return {"ok": False, "error": "payments not configured"}

    try:
        if sandbox:
            link = {"id": f"sandbox_{uuid.uuid4().hex[:10]}", "url": f"https://rzp.io/sandbox/{uuid.uuid4().hex[:8]}"}
        else:
            link = await _create_razorpay_link(idempotency_key, amount_inr, customer_name, phone,
                                               f"{product_name} ({plan})")
        message = f"Namaste {customer_name}, {product_name} ({plan}) ke liye aapka payment link: {link['url']} Amount: Rs {int(amount_inr)}"
        sent = await send_message(phone, message, channel=channel, template_params=[customer_name, product_name, link["url"], str(int(amount_inr))])
        status = "sent" if sent.get("ok") else "failed"
        await _persist(idempotency_key=idempotency_key, lead_id=lead_id, call_id=call_id, sku=sku, plan=plan,
                       offer_id=offer_id, amount_inr=amount_inr, channel=channel, provider_link_id=link["id"],
                       url=link["url"], status=status, error=sent.get("error"))
        m.record_vendor_call("payments", ok=sent.get("ok", False))
        if not sent.get("ok"):
            return {"ok": False, "error": sent.get("error")}
        result = {"ok": True, "url": link["url"], "amount_inr": amount_inr, "sandbox": sandbox or sent.get("sandbox", False)}
        _sent_in_process[idempotency_key] = result
        logger.info(f"PAYMENT LINK {'(sandbox) ' if result['sandbox'] else ''}sent: lead={lead_id} amount={amount_inr} "
                    f"channel={channel} url={link['url']} key={idempotency_key}")
        return result
    except Exception as e:
        m.record_vendor_call("payments", ok=False)
        logger.exception("Payment link creation failed")
        return {"ok": False, "error": str(e)}
