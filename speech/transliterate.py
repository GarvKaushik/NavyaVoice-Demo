"""Devanagari to Roman Hinglish, so local STT output reads like Sarvam's translit mode.

The guards, stage signals and tools all read Roman Hinglish ("call mat karna"), so a
spelling miss here can hide an opt-out from the fast path. Two layers keep this safe:
a fixed word list for everything those patterns depend on, then indic-transliteration
plus Hindi schwa deletion for all other words.

Weak spots: English loanwords and names not in the word list come out phonetic
("kontekt"), and spelling can differ from what the LLM or lexicon expects.
"""

import re
import unicodedata
from typing import Callable, Dict, List, Optional, Tuple

_DEVANAGARI_RUN = re.compile(r"[ऀ-ॣ०-ॿ]+")
_NUKTA = "़"

# Words the fast-path guard, stage signals, time parser and lexicon rely on, and common loanwords
WORDS: Dict[str, str] = {
    # Yes, no and basics
    "हाँ": "haan", "हां": "haan", "हा": "haan", "जी": "ji", "नहीं": "nahi", "नही": "nahi", "ना": "na", "मत": "mat",
    "है": "hai", "हैं": "hain", "हूं": "hoon", "हूँ": "hoon", "हुँ": "hun", "हो": "ho", "मैं": "main", "मेरा": "mera",
    "मेरे": "mere", "मुझे": "mujhe", "आप": "aap", "आपका": "aapka", "वो": "woh", "वह": "woh", "ये": "yeh", "यह": "yeh",
    "उनका": "unka", "उसका": "uska", "किसको": "kisko", "कोई": "koi", "क्या": "kya", "क्यों": "kyun", "कैसे": "kaise",
    "कितना": "kitna", "कितने": "kitne", "कितनी": "kitni", "कब": "kab", "कौन": "kaun", "अच्छा": "accha", "ठीक": "theek",
    "सही": "sahi", "बिल्कुल": "bilkul", "ज़रूर": "zaroor", "जरूर": "zaroor", "ओके": "ok", "यस": "yes", "नो": "no",
    "बोलिए": "boliye", "बोलिये": "boliye", "बोलो": "bolo", "बताइए": "bataiye", "बताइये": "bataiye", "बताओ": "batao",
    # Opt-out and calling
    "कॉल": "call", "काल": "call", "फोन": "phone", "फ़ोन": "phone", "नंबर": "number", "नम्बर": "number", "लिस्ट": "list",
    "डेटाबेस": "database", "डीएनडी": "dnd", "हटा": "hata", "हटाओ": "hatao", "हटाइए": "hatao", "निकाल": "nikal",
    "निकालो": "nikalo", "रिमूव": "remove", "डिलीट": "delete", "डाल": "daal", "डालो": "dalo", "करो": "karo",
    "करना": "karna", "करें": "karein", "करिए": "kariye", "कीजिए": "kijiye", "कीजिये": "kijiye", "कर": "kar", "दो": "do",
    "बंद": "band", "दोबारा": "dobara", "दुबारा": "dubara", "आगे": "aage", "फिर": "phir", "से": "se", "कभी": "kabhi",
    "अनसब्सक्राइब": "unsubscribe",
    # Later and busy
    "बाद": "baad", "में": "mein", "बात": "baat", "बिज़ी": "busy", "बिजी": "busy", "मीटिंग": "meeting", "ड्राइव": "drive",
    "रहा": "raha", "रही": "rahi", "सकता": "sakta", "सकती": "sakti", "अभी": "abhi", "कल": "kal", "परसों": "parso",
    "आज": "aaj", "शाम": "shaam", "सुबह": "subah", "दोपहर": "dopahar", "रात": "raat", "को": "ko", "बजे": "baje",
    "घंटे": "ghante", "घंटा": "ghanta", "मिनट": "minute",
    # Wrong person, human, complaint
    "गलत": "galat", "ग़लत": "galat", "रॉन्ग": "wrong", "लगाया": "lagaya", "किया": "kiya", "नाम": "naam", "रहता": "rehta",
    "इंसान": "insaan", "आदमी": "aadmi", "मैनेजर": "manager", "सीनियर": "senior", "सुपरवाइजर": "supervisor",
    "एजेंट": "agent", "ट्रांसफर": "transfer", "कराओ": "karao", "शिकायत": "shikayat", "कंप्लेंट": "complaint",
    "दर्ज": "darj", "करनी": "karni", "करूंगा": "karunga", "करूंगी": "karungi", "फ्रॉड": "fraud", "स्कैम": "scam",
    "पुलिस": "police",
    # Numbers
    "एक": "ek", "तीन": "teen", "चार": "chaar", "पांच": "paanch", "पाँच": "paanch", "छह": "chhah", "छः": "chhah",
    "सात": "saat", "आठ": "aath", "नौ": "nau", "दस": "das", "ग्यारह": "gyarah", "बारह": "barah",
    # Sales words and loanwords
    "प्राइस": "price", "मेहंगा": "mehenga", "महंगा": "mehenga", "सस्ता": "sasta", "बजट": "budget", "डिस्काउंट": "discount",
    "ऑफर": "offer", "प्लान": "plan", "डेमो": "demo", "लिंक": "link", "पेमेंट": "payment", "व्हाट्सएप": "WhatsApp",
    "व्हाट्सऐप": "WhatsApp", "वॉट्सएप": "WhatsApp", "एसएमएस": "SMS", "सीआरएम": "CRM", "एक्सेल": "excel", "सॉफ्टवेयर": "software",
    "मैनुअल": "manual", "मैन्युअली": "manually", "लीड": "lead", "लीड्स": "leads", "सेल्स": "sales", "टीम": "team",
    "मैन्युली": "manually", "एक्सल": "excel", "शीट": "sheet", "अपडेट": "update", "व्हाट्सएऐप": "WhatsApp", "कस्टमर": "customer",
    "प्रोडक्ट": "product", "सिस्टम": "system", "सर": "sir", "मैम": "ma'am", "कंपनी": "company", "बिज़नेस": "business",
    "फॉलो": "follow", "अप": "up", "कॉल्स": "calls", "टाइम": "time", "स्पीकिंग": "speaking", "हम्म": "hmm", "हम्मा": "hmm", "कंज्यूमिंग": "consuming", "कंजूमिंग": "consuming", "प्रो": "pro", "स्टार्टर": "starter", "नेक्सस": "Nexus",
}

_TOKEN = re.compile(r"((?:[kgcjtdnpbmyrlvshfzqTDNSCRLG]|~[nN]|\.[nN]|\.)*)(R\^i|ai|au|[aAiIuUeoEO])?([M]|\.N)?")
_CONSONANT_MAP = [("~N", "n"), ("~n", "n"), ("Ch", "chh"), ("Sh", "sh"), ("T", "t"), ("D", "d"), ("N", "n"), (".", "")]
_VOWELS = {"a": "a", "i": "i", "I": "i", "u": "u", "U": "u", "e": "e", "ai": "ai", "o": "o", "au": "au", "R^i": "ri",
           "E": "e", "O": "o"}


def _itrans(word: str) -> str:
    from indic_transliteration import sanscript

    return sanscript.transliterate(word, sanscript.DEVANAGARI, sanscript.ITRANS)


def _syllables(word: str) -> List[Tuple[str, str, str]]:
    """Splits ITRANS text into (consonants, vowel, nasal) groups."""
    out = []
    for consonants, vowel, nasal in _TOKEN.findall(word):
        if consonants or vowel or nasal:
            out.append((consonants, vowel or "", nasal or ""))
    return out


def _is_single_consonant(consonants: str) -> bool:
    return bool(re.fullmatch(r"[kgcjtdnpbmyrlvshfzqTDNSC]h?", consonants)) or consonants in ("sh", "Sh", "ch", "Ch")


def _schwa_delete(parts: List[List[str]]) -> None:
    """Hindi drops the written short 'a' at the end of a word and in the middle of many: करना is karna, not karana."""
    if len(parts) > 1 and parts[-1][1] == "a" and _is_single_consonant(parts[-1][0]) and not parts[-1][2]:
        parts[-1][1] = ""
    for i in range(len(parts) - 2, 0, -1):
        current, previous, following = parts[i], parts[i - 1], parts[i + 1]
        if (current[1] == "a" and not current[2] and _is_single_consonant(current[0]) and previous[1]
                and following[1] and _is_single_consonant(following[0])):
            current[1] = ""


def _rule_based(word: str) -> str:
    text = _itrans(word).replace("aॉ", "o").replace("ॉ", "o")
    parts = [list(p) for p in _syllables(text)]
    _schwa_delete(parts)
    out = []
    for index, (consonants, vowel, nasal) in enumerate(parts):
        for source, target in _CONSONANT_MAP:
            consonants = consonants.replace(source, target)
        if vowel == "A":
            spoken = "a" if index == len(parts) - 1 and not nasal else "aa"
        else:
            spoken = _VOWELS.get(vowel, "")
        out.append(consonants + spoken + ("n" if nasal else ""))
    return "".join(out).lower()


# Exact-spelling lookup that also finds words written with or without a nukta (फ़ोन, फोन)
_LOOKUP = {unicodedata.normalize("NFC", k).replace(_NUKTA, ""): v
           for k, v in ((unicodedata.normalize("NFD", key), value) for key, value in WORDS.items())}
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


class HinglishTransliterator:
    """Pluggable: pass another word-level function to swap the rule-based step."""

    def __init__(self, fallback: Optional[Callable[[str], str]] = None):
        self.fallback = fallback or _rule_based

    def _word(self, match: re.Match) -> str:
        word = match.group(0)
        known = _LOOKUP.get(unicodedata.normalize("NFD", word).replace(_NUKTA, ""))
        if known:
            return known
        return self.fallback(unicodedata.normalize("NFC", word))

    def to_roman(self, text: str) -> str:
        """Roman text passes through unchanged, so this is safe on any engine's output."""
        if not text:
            return ""
        text = text.translate(_DIGITS).replace("।", ".").replace("॥", ".")
        return re.sub(r"\s+", " ", _DEVANAGARI_RUN.sub(self._word, text)).strip()


_default: Optional[HinglishTransliterator] = None


def to_roman(text: str) -> str:
    global _default
    if _default is None:
        _default = HinglishTransliterator()
    return _default.to_roman(text)
