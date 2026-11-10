"""Shared-secret check between the pipeline and the STT and TTS services.

Both sides read SPEECH_SERVER_TOKEN. The pipeline sends it as a bearer token; a service with the setting
empty accepts every request, which is only safe while it listens on the local machine.
"""

import hmac
from typing import Mapping

from config.settings import get_settings


def auth_headers() -> dict:
    token = get_settings().SPEECH_SERVER_TOKEN
    return {"Authorization": f"Bearer {token}"} if token else {}


def authorized(headers: Mapping[str, str]) -> bool:
    token = get_settings().SPEECH_SERVER_TOKEN
    if not token:
        return True
    return hmac.compare_digest(headers.get("authorization", ""), f"Bearer {token}")
