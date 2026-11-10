"""Local text-to-speech with Piper: a REST-style client like the others, and a LiveKit adapter for live calls.

Piper runs on the CPU in this process. The voice downloads on first use (no key needed) and then works offline.
"""

import asyncio
import logging
import os
import threading
import uuid
from typing import Optional

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, tts as lk_tts

from config.settings import get_settings
from speech.bulbul_tts import BulbulTTSError, pcm_to_wav
from speech.devanagari import to_devanagari
from speech.normalizer import HinglishNormalizer

logger = logging.getLogger(__name__)
VOICE_REPO = "rhasspy/piper-voices"

_voice = None
_voice_lock = threading.Lock()


def _voice_path(voice: str, model_dir: str) -> str:
    """hi_IN-priyamvada-medium lives at hi/hi_IN/priyamvada/medium/ in the voice repository."""
    locale, name, quality = voice.split("-")
    relative = f"{locale.split('_')[0]}/{locale}/{name}/{quality}/{voice}.onnx"
    path = os.path.join(model_dir, relative)
    if not (os.path.exists(path) and os.path.exists(path + ".json")):
        from huggingface_hub import hf_hub_download

        logger.info(f"Downloading voice {voice}")
        for filename in (relative, relative + ".json"):
            hf_hub_download(VOICE_REPO, filename, local_dir=model_dir)
    return path


def load_voice():
    """Loads the voice once per process; it takes a few seconds."""
    global _voice
    with _voice_lock:
        if _voice is None:
            from piper import PiperVoice

            s = get_settings()
            _voice = PiperVoice.load(_voice_path(s.PIPER_VOICE, s.PIPER_MODEL_DIR))
    return _voice


def render(spoken: str) -> bytes:
    """Normalized text to 16-bit mono PCM. Blocks, so async callers run it in a thread."""
    from piper import SynthesisConfig

    config = SynthesisConfig(length_scale=get_settings().PIPER_LENGTH_SCALE)
    return b"".join(chunk.audio_int16_bytes for chunk in load_voice().synthesize(to_devanagari(spoken), syn_config=config))


class PiperTTSClient:
    """Used for the browser script console and pre-rendered phrases."""

    def __init__(self):
        s = get_settings()
        self.model = "piper"
        self.voice_id = s.PIPER_VOICE
        self.sample_rate = s.PIPER_SAMPLE_RATE
        self.last_error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return True

    async def synthesize_pcm(self, text: str, *, normalize: bool = True) -> bytes:
        spoken = HinglishNormalizer.normalize(text) if normalize else text
        if not spoken:
            raise BulbulTTSError("empty text")
        try:
            return await asyncio.to_thread(render, spoken)
        except Exception as e:
            self.last_error = repr(e)
            raise BulbulTTSError(self.last_error) from e

    async def synthesize_wav(self, text: str, *, normalize: bool = True) -> bytes:
        return pcm_to_wav(await self.synthesize_pcm(text, normalize=normalize), self.sample_rate)


class PiperLiveTTS(lk_tts.TTS):
    """The worker hands over one normalized sentence at a time, so whole-sentence synthesis is enough."""

    def __init__(self):
        super().__init__(capabilities=lk_tts.TTSCapabilities(streaming=False), sample_rate=get_settings().PIPER_SAMPLE_RATE,
                         num_channels=1)

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "PiperStream":
        return PiperStream(tts=self, input_text=text, conn_options=conn_options)


class PiperStream(lk_tts.ChunkedStream):
    async def _run(self, output_emitter: lk_tts.AudioEmitter) -> None:
        output_emitter.initialize(request_id=uuid.uuid4().hex, sample_rate=self._tts.sample_rate, num_channels=1,
                                  mime_type="audio/pcm")
        output_emitter.push(await asyncio.to_thread(render, self.input_text))
        output_emitter.flush()
