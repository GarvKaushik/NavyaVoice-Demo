"""Speech processing components: Hinglish normalizer, lexicon, phrase cache and REST TTS clients.

The STT and TTS providers for calls are chosen in agent.worker.
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
