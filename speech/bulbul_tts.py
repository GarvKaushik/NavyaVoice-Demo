"""Sarvam Bulbul REST client.

Used by the browser test console and the phrase pre-renderer. Live calls use
the LiveKit Sarvam plugin's streaming TTS instead.
"""

import base64
import io
import logging
import wave
from typing import Optional

import httpx

from config.settings import get_settings
from observability import metrics as m
from .normalizer import HinglishNormalizer

logger = logging.getLogger(__name__)


class BulbulTTSError(RuntimeError):
    pass


_http: Optional[httpx.AsyncClient] = None


def shared_http() -> httpx.AsyncClient:
    """One keep-alive client for Sarvam REST calls (saves a TLS handshake per request)."""
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0),
                                  limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120))
    return _http


class BulbulTTSClient:
    """Client for Sarvam Bulbul speech synthesis."""

    def __init__(self, api_key: Optional[str] = None, voice_id: Optional[str] = None,
                 sample_rate: Optional[int] = None, model: Optional[str] = None):
        s = get_settings()
        self.api_key = api_key or s.SARVAM_API_KEY
        self.voice_id = voice_id or s.BULBUL_TTS_VOICE_ID
        self.sample_rate = sample_rate or s.BULBUL_TTS_SAMPLE_RATE
        self.model = model or s.BULBUL_TTS_MODEL
        self.pitch = s.BULBUL_TTS_PITCH
        self.pace = s.BULBUL_TTS_PACE
        self.url = s.BULBUL_TTS_URL
        self.last_error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("mock")

    async def synthesize_wav(self, text: str, *, normalize: bool = True) -> bytes:
        """Returns a WAV file (16-bit mono) for the text. Raises BulbulTTSError on failure."""
        spoken = HinglishNormalizer.normalize(text) if normalize else text
        if not spoken:
            raise BulbulTTSError("empty text")
        if not self.configured:
            raise BulbulTTSError("SARVAM_API_KEY not configured")
        payload = {
            "text": spoken,
            "target_language_code": "hi-IN",
            "speaker": self.voice_id,
            "model": self.model,
            "speech_sample_rate": self.sample_rate,
            "pace": self.pace,
            "enable_preprocessing": False,  # our deterministic normalizer runs first
        }
        if self.model in ("bulbul:v2",):
            payload["pitch"] = self.pitch
        headers = {"api-subscription-key": self.api_key, "Content-Type": "application/json"}
        resp = await shared_http().post(self.url, json=payload, headers=headers)
        if resp.status_code != 200:
            m.record_vendor_call("tts", ok=False)
            self.last_error = f"{resp.status_code}: {resp.text[:300]}"
            raise BulbulTTSError(self.last_error)
        m.record_vendor_call("tts", ok=True)
        audios = resp.json().get("audios") or []
        if not audios:
            raise BulbulTTSError("no audio in response")
        return base64.b64decode(audios[0])

    async def synthesize_pcm(self, text: str, *, normalize: bool = True) -> bytes:
        """Raw 16-bit mono PCM at self.sample_rate (for the phrase cache)."""
        wav_bytes = await self.synthesize_wav(text, normalize=normalize)
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                raise BulbulTTSError("unexpected WAV format")
            if w.getframerate() != self.sample_rate:
                raise BulbulTTSError(f"expected {self.sample_rate} Hz, got {w.getframerate()} Hz")
            return w.readframes(w.getnframes())

    async def synthesize_base64(self, text: str) -> Optional[str]:
        """Base64 WAV for the browser console, or None (console falls back to browser speech)."""
        try:
            return base64.b64encode(await self.synthesize_wav(text)).decode()
        except (BulbulTTSError, httpx.HTTPError) as e:
            self.last_error = str(e)
            logger.warning(f"Bulbul TTS failed: {e}")
            return None


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()
