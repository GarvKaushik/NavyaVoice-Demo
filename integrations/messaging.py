"""WhatsApp and SMS delivery via Plivo.

SMS in India must use a DLT-registered header and template; WhatsApp must use
an approved template. Outside production without a configured sender, the
message is logged instead of sent (sandbox).
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)


async def send_message(to: str, text: str, *, channel: str = "whatsapp",
                       template_params: Optional[List[str]] = None) -> Dict[str, Any]:
    s = get_settings()
    configured = (channel == "whatsapp" and s.PLIVO_WHATSAPP_SENDER) or (channel == "sms" and s.PLIVO_SMS_SENDER_ID)
    if not configured or s.PLIVO_AUTH_ID.startswith("MOCK"):
        if s.is_production:
            return {"ok": False, "error": f"{channel} sender not configured"}
        logger.info(f"[sandbox {channel}] to={to}: {text}")
        return {"ok": True, "sandbox": True}

    url = f"https://api.plivo.com/v1/Account/{s.PLIVO_AUTH_ID}/Message/"
    if channel == "whatsapp":
        body: Dict[str, Any] = {
            "src": s.PLIVO_WHATSAPP_SENDER, "dst": to, "type": "whatsapp",
            "template": {
                "name": s.PLIVO_WHATSAPP_PAYMENT_TEMPLATE, "language": "en",
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in (template_params or [])]}],
            },
        }
    else:
        body = {"src": s.PLIVO_SMS_SENDER_ID, "dst": to, "text": text,
                "dlt_entity_id": s.PLIVO_DLT_ENTITY_ID, "dlt_template_id": s.PLIVO_DLT_PAYMENT_TEMPLATE_ID}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=body, auth=(s.PLIVO_AUTH_ID, s.PLIVO_AUTH_TOKEN))
            resp.raise_for_status()
        return {"ok": True}
    except httpx.HTTPError as e:
        logger.error(f"Plivo {channel} send failed: {e!r}")
        return {"ok": False, "error": str(e)}
