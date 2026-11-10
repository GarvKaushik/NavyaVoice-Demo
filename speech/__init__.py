"""Speech processing components: Hinglish normalizer, lexicon, phrase cache and Bulbul REST client.

Streaming STT/TTS on calls uses the official LiveKit Sarvam plugin (see agent.worker).
"""

from .normalizer import HinglishNormalizer
from .phrase_cache import PreRenderedPhraseCache, SentenceAudioCache
from .bulbul_tts import BulbulTTSClient

__all__ = [
    "HinglishNormalizer",
    "PreRenderedPhraseCache",
    "SentenceAudioCache",
    "BulbulTTSClient",
]
