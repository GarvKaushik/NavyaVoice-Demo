"""Pre-rendered audio phrase cache for zero-latency instant playback.

Two layers:
- PreRenderedPhraseCache: fixed scripted lines (disclosure, opt-out confirmation,
  hold lines, fillers) rendered ahead of time by `python -m speech.prerender`
  and loaded from disk at worker start.
- SentenceAudioCache: an LRU of every sentence synthesized at runtime, so common
  short answers ("Ji bilkul sir.") are served from cache on later turns and calls.

Audio is stored as raw 16-bit mono PCM at the TTS sample rate (8 kHz).
"""

import hashlib
import logging
import os
from collections import OrderedDict
from typing import Dict, Optional, Tuple

from config.settings import get_settings
from speech.normalizer import HinglishNormalizer

logger = logging.getLogger(__name__)


def audio_key(text: str, voice: str, model: str, sample_rate: int, pitch: float, pace: float) -> str:
    raw = f"{text.strip()}|{voice}|{model}|{sample_rate}|{pitch}|{pace}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class PreRenderedPhraseCache:
    """Manages pre-rendered audio buffers and their canonical text representations."""

    # Canonical phrase IDs
    DISCLOSURE = "disclosure"
    GREETING_AND_DISCLOSURE = "disclosure"  # backwards-compatible alias
    OPT_OUT_CONFIRMED = "opt_out_confirmed"
    HOLD_CHECKING_DETAILS = "hold_checking_details"
    LLM_FILLER = "llm_filler"
    SILENCE_REPROMPT = "silence_reprompt"
    SILENCE_CLOSE = "silence_close"
    NETWORK_ISSUE_FALLBACK = "network_issue_fallback"
    TRANSFER_TO_AGENT = "transfer_to_agent"
    TRANSFER_UNAVAILABLE = "transfer_unavailable"
    SAFE_GUARD_REPLACEMENT = "safe_guard_replacement"
    WRONG_PERSON = "wrong_person"
    ASK_CALLBACK_TIME = "ask_callback_time"
    ABUSE_CLOSE = "abuse_close"
    COMPLAINT_LOGGED = "complaint_logged"
    PERMISSION_DENIED_CLOSE = "permission_denied_close"
    GOODBYE = "goodbye"
    ACK_SHORT = "ack_short"

    CANONICAL_TEXTS: Dict[str, str] = {
        DISCLOSURE: (
            "Namaste! Main {company} ki taraf se {persona} bol rahi hoon, ek AI assistant. "
            "Yeh call quality aur training ke liye record ho rahi hai."
        ),
        OPT_OUT_CONFIRMED: (
            "Bilkul sir, maine aapka number hamari calling list se hata diya hai. "
            "Aapko aage se hamari taraf se call nahi aayega. Aapka din shubh ho."
        ),
        HOLD_CHECKING_DETAILS: "Ji ek second rukiye, main system mein details check karke batati hoon.",
        LLM_FILLER: "Ji, ek second.",
        SILENCE_REPROMPT: "Aap sun pa rahe hain sir?",
        SILENCE_CLOSE: (
            "Lagta hai aapki aawaz nahi aa rahi hai sir. Main aapko thodi der mein dobara call karti hoon. Dhanyawaad."
        ),
        NETWORK_ISSUE_FALLBACK: "Network issue aa raha hai sir, main aapko thodi der mein call karti hoon.",
        TRANSFER_TO_AGENT: (
            "Ji zaroor, main aapki call hamare senior product expert ko transfer kar rahi hoon, kripya line par bane rahiye."
        ),
        TRANSFER_UNAVAILABLE: (
            "Abhi hamare sabhi experts busy hain sir. Main unse aapko callback karwa deti hoon. Dhanyawaad."
        ),
        SAFE_GUARD_REPLACEMENT: "Main exact detail confirm karke batati hoon.",
        WRONG_PERSON: "Maaf kijiyega, lagta hai galat number lag gaya. Aapka din shubh ho.",
        ASK_CALLBACK_TIME: "Ji bilkul, koi baat nahi. Aapko kab call karna theek rahega?",
        ABUSE_CLOSE: "Main samajh sakti hoon aap naraz hain. Main yeh call yahin khatam karti hoon. Dhanyawaad.",
        COMPLAINT_LOGGED: (
            "Ji, maine aapki shikayat darj kar li hai. Aap {complaint_contact} par bhi sampark kar sakte hain. Dhanyawaad."
        ),
        PERMISSION_DENIED_CLOSE: "Koi baat nahi sir, aapka samay dene ke liye dhanyawaad. Aapka din shubh ho.",
        GOODBYE: "Aapka bahut dhanyawaad sir. Aapka din shubh ho.",
        ACK_SHORT: "Ji bilkul.",
    }

    def __init__(self, cache_dir: Optional[str] = None, voice: Optional[str] = None, company: Optional[str] = None,
                 persona: Optional[str] = None, complaint_contact: Optional[str] = None):
        s = get_settings()
        self.cache_dir = cache_dir or s.PHRASE_CACHE_DIR
        self.voice = voice or s.tts_voice
        self.model = s.tts_model
        self.sample_rate = s.tts_sample_rate
        self.pitch = s.BULBUL_TTS_PITCH
        self.pace = s.BULBUL_TTS_PACE
        self._fmt = {
            "company": company or s.COMPANY_NAME,
            "persona": persona or s.AGENT_PERSONA_NAME,
            "complaint_contact": complaint_contact or s.COMPLAINT_CONTACT,
        }
        # Maps phrase_id -> raw 16-bit PCM audio
        self._audio_cache: Dict[str, bytes] = {}

    # ------------------------------------------------------------------
    def get_text(self, phrase_id: str) -> Optional[str]:
        """Returns the spoken transcript for a cached phrase."""
        template = self.CANONICAL_TEXTS.get(phrase_id)
        return template.format(**self._fmt) if template else None

    def key_for(self, phrase_id: str) -> str:
        return audio_key(self.get_text(phrase_id) or "", self.voice, self.model, self.sample_rate, self.pitch, self.pace)

    def path_for(self, phrase_id: str) -> str:
        return os.path.join(self.cache_dir, self.voice, f"{self.key_for(phrase_id)}.pcm")

    def load(self) -> int:
        """Loads every pre-rendered phrase available on disk. Returns how many loaded."""
        loaded = 0
        for phrase_id in self.CANONICAL_TEXTS:
            path = self.path_for(phrase_id)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    self._audio_cache[phrase_id] = f.read()
                loaded += 1
        missing = len(self.CANONICAL_TEXTS) - loaded
        if missing:
            logger.warning(f"{missing} phrases not pre-rendered; run `python -m speech.prerender`")
        return loaded

    def save(self, phrase_id: str, audio_bytes: bytes) -> str:
        path = self.path_for(phrase_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(audio_bytes)
        self._audio_cache[phrase_id] = audio_bytes
        return path

    def register_audio(self, phrase_id: str, audio_bytes: bytes) -> None:
        """Loads pre-rendered audio bytes into cache."""
        self._audio_cache[phrase_id] = audio_bytes

    def get_audio(self, phrase_id: str) -> Optional[bytes]:
        """Retrieves pre-rendered audio bytes if present."""
        return self._audio_cache.get(phrase_id)

    def is_cached(self, phrase_id: str) -> bool:
        """Checks if pre-rendered audio exists in memory."""
        return phrase_id in self._audio_cache

    def get_phrase_for_opt_out(self) -> Tuple[str, Optional[bytes]]:
        """Returns text and cached audio for instant DND opt-out."""
        return self.get_text(self.OPT_OUT_CONFIRMED), self._audio_cache.get(self.OPT_OUT_CONFIRMED)

    def get_phrase_for_safe_guard(self) -> Tuple[str, Optional[bytes]]:
        """Returns replacement text when Output Guard blocks an unverified statement."""
        return self.get_text(self.SAFE_GUARD_REPLACEMENT), self._audio_cache.get(self.SAFE_GUARD_REPLACEMENT)

    def find_by_text(self, text: str) -> Optional[str]:
        """Returns the phrase_id whose text equals this sentence, if any."""
        wanted = text.strip()
        for phrase_id in self.CANONICAL_TEXTS:
            if self.get_text(phrase_id) == wanted:
                return phrase_id
        return None


class SentenceAudioCache:
    """LRU of synthesized sentences, memory first then disk (shared per pod)."""

    def __init__(self, cache_dir: Optional[str] = None, max_items: Optional[int] = None, voice: Optional[str] = None):
        s = get_settings()
        self.voice = voice or s.tts_voice
        self.dir = os.path.join(cache_dir or s.PHRASE_CACHE_DIR, "runtime", self.voice)
        self.max_items = max_items or s.TTS_RUNTIME_CACHE_MAX_ITEMS
        self._s = s
        self._mem: "OrderedDict[str, bytes]" = OrderedDict()

    def _key(self, text: str) -> str:
        s = self._s
        # Keyed on what is actually spoken, so a pronunciation change never replays old audio
        return audio_key(HinglishNormalizer.normalize(text), self.voice, s.tts_model, s.tts_sample_rate,
                         s.BULBUL_TTS_PITCH, s.BULBUL_TTS_PACE)

    def get(self, text: str) -> Optional[bytes]:
        key = self._key(text)
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        path = os.path.join(self.dir, f"{key}.pcm")
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            self._remember(key, data)
            return data
        return None

    def put(self, text: str, audio: bytes) -> None:
        # Only cache short, reusable sentences; long ones are rarely repeated verbatim
        if not audio or len(text) > 120:
            return
        key = self._key(text)
        self._remember(key, audio)
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(os.path.join(self.dir, f"{key}.pcm"), "wb") as f:
                f.write(audio)
        except OSError as e:
            logger.debug(f"Sentence cache disk write skipped: {e}")

    def _remember(self, key: str, data: bytes) -> None:
        self._mem[key] = data
        self._mem.move_to_end(key)
        while len(self._mem) > self.max_items:
            self._mem.popitem(last=False)
