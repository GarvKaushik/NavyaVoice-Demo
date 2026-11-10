"""OpenAI speech REST client, with the same interface as BulbulTTSClient, and the choice between them."""

import logging
from typing import Optional

import httpx

from config.settings import get_settings
from speech.bulbul_tts import BulbulTTSClient, BulbulTTSError, pcm_to_wav, shared_http
from speech.normalizer import HinglishNormalizer

logger = logging.getLogger(__name__)


class OpenAITTSClient:
    """Used for the browser script console and pre-rendered phrases. Live calls use the LiveKit OpenAI plugin."""

    def __init__(self, http: Optional[httpx.AsyncClient] = None):
        s = get_settings()
        self.api_key = s.OPENAI_API_KEY
        self.url = s.OPENAI_BASE_URL.rstrip("/") + "/audio/speech"
        self.model = s.OPENAI_TTS_MODEL
        self.voice_id = s.OPENAI_TTS_VOICE
        self.instructions = s.OPENAI_TTS_INSTRUCTIONS
        self.sample_rate = s.OPENAI_TTS_SAMPLE_RATE
        self.speed = s.OPENAI_TTS_SPEED
        self.last_error: Optional[str] = None
        self._http = http

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def synthesize_pcm(self, text: str, *, normalize: bool = True) -> bytes:
        """Raw 16-bit mono PCM at self.sample_rate. Raises BulbulTTSError on failure."""
        spoken = HinglishNormalizer.normalize(text) if normalize else text
        if not spoken:
            raise BulbulTTSError("empty text")
        if not self.configured:
            raise BulbulTTSError("OPENAI_API_KEY not configured")
        payload = {"model": self.model, "voice": self.voice_id, "input": spoken, "response_format": "pcm", "speed": self.speed}
        if self.instructions and self.model == "gpt-4o-mini-tts":
            payload["instructions"] = self.instructions
        resp = await (self._http or shared_http()).post(self.url, json=payload,
                                                        headers={"Authorization": f"Bearer {self.api_key}"})
        if resp.status_code != 200:
            self.last_error = f"{resp.status_code}: {resp.text[:300]}"
            raise BulbulTTSError(self.last_error)
        return resp.content

    async def synthesize_wav(self, text: str, *, normalize: bool = True) -> bytes:
        return pcm_to_wav(await self.synthesize_pcm(text, normalize=normalize), self.sample_rate)


def create_tts_client():
    provider = get_settings().TTS_PROVIDER
    if provider == "server":
        from speech.server_tts import ServerTTSClient

        return ServerTTSClient()
    if provider == "piper":
        from speech.piper_tts import PiperTTSClient

        return PiperTTSClient()
    return OpenAITTSClient() if provider == "openai" else BulbulTTSClient()
