"""Romanized Hindi and English number words.

Shared by the TTS normalizer (numbers -> words) and the output guard
(words -> numbers), so a price the LLM spells out in words is checked the
same way as one written in digits.
"""

import re
from typing import Dict, List, Optional, Tuple

HINDI_0_99: List[str] = [
    "shunya", "ek", "do", "teen", "chaar", "paanch", "chhah", "saat", "aath", "nau",
    "das", "gyaarah", "baarah", "terah", "chaudah", "pandrah", "solah", "satrah", "athaarah", "unnees",
    "bees", "ikkees", "baaees", "teyees", "chaubees", "pachchees", "chhabbees", "sattaees", "atthaees", "untees",
    "tees", "ikattees", "battees", "taintees", "chauntees", "paintees", "chhattees", "saintees", "adtees", "untaalees",
    "chaalees", "iktaalees", "bayaalees", "taintaalees", "chavaalees", "paintaalees", "chhiyaalees", "saintaalees", "adtaalees", "unchaas",
    "pachaas", "ikyaavan", "baavan", "tirpan", "chauvan", "pachpan", "chhappan", "sattaavan", "atthaavan", "unsath",
    "saath", "iksath", "baasath", "tirsath", "chausath", "painsath", "chhiyaasath", "sadsath", "adsath", "unhattar",
    "sattar", "ikhattar", "bahattar", "tihattar", "chauhattar", "pachhattar", "chhihattar", "satattar", "athhattar", "unaasi",
    "assi", "ikyaasi", "bayaasi", "tiraasi", "chauraasi", "pachaasi", "chhiyaasi", "sattaasi", "atthaasi", "nawaasi",
    "nabbe", "ikyaanve", "baanve", "tiraanve", "chauraanve", "pachaanve", "chhiyaanve", "sattaanve", "atthaanve", "ninyaanve",
]

HINDI_POWERS: List[Tuple[int, str]] = [
    (10_000_000, "crore"),
    (100_000, "lakh"),
    (1_000, "hazaar"),
    (100, "sau"),
]

# Spelling variants LLMs and speakers commonly produce
_HINDI_VARIANTS: Dict[str, int] = {
    "char": 4, "panch": 5, "chhe": 6, "chhey": 6, "che": 6, "chah": 6, "saath": 60, "sath": 60,
    "gyarah": 11, "barah": 12, "pandrah": 15, "pachis": 25, "pachees": 25, "pachchis": 25,
    "pachas": 50, "pachaas": 50, "chalis": 40, "chaalis": 40, "tis": 30, "bis": 20,
    "sattar": 70, "assi": 80, "nabbe": 90, "ninnanve": 99, "ninyanve": 99, "nindyanve": 99,
    "hazar": 1000, "hajaar": 1000, "hajar": 1000, "dedh": 1.5, "dhai": 2.5, "dhaai": 2.5, "sava": 1.25, "saade": 0.5,
}

ENGLISH_WORDS: Dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

MULTIPLIERS: Dict[str, int] = {
    "sau": 100, "hundred": 100,
    "hazaar": 1000, "hazar": 1000, "hajaar": 1000, "hajar": 1000, "thousand": 1000,
    "lakh": 100_000, "lac": 100_000, "lakhs": 100_000,
    "crore": 10_000_000, "karod": 10_000_000, "crores": 10_000_000,
}

# Words after a bare number word that make it a commercial figure
VALUE_CONTEXT_WORDS = {
    "rupaye", "rupaiye", "rupees", "rupee", "rs", "inr", "percent", "pratishat", "pratishat",
    "%", "takka", "off",
}

WORD_TO_VALUE: Dict[str, float] = {w: i for i, w in enumerate(HINDI_0_99)}
WORD_TO_VALUE.update(_HINDI_VARIANTS)
WORD_TO_VALUE.update(ENGLISH_WORDS)

# Plain small words that are ordinary Hindi vocabulary, never counted unless
# followed by a multiplier or value word ("bhej do", "ek second", "ke saath").
_AMBIGUOUS = {"do", "ek", "saath", "sath", "nau", "che", "tis", "bis", "saade", "sava"}


def int_to_hindi(num: int) -> str:
    """Converts an integer to romanized Hindi words (Indian numbering)."""
    if num < 0:
        return "minus " + int_to_hindi(-num)
    if num < 100:
        return HINDI_0_99[num]
    for divisor, label in HINDI_POWERS:
        if num >= divisor:
            quotient, remainder = divmod(num, divisor)
            head = f"{int_to_hindi(quotient)} {label}"
            return head if remainder == 0 else f"{head} {int_to_hindi(remainder)}"
    return str(num)


_TOKEN_RE = re.compile(r"[a-zA-Z%]+")


def extract_word_numbers(text: str) -> List[float]:
    """Finds numbers written as words that carry a commercial meaning.

    A run of number words counts when it contains a multiplier ("pandrah sau",
    "ek hazaar") or is followed by a currency/percent word ("pachaas percent").
    Isolated small words ("bhej do", "ek minute") are ignored.
    """
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    values: List[float] = []
    i = 0
    while i < len(tokens):
        if tokens[i] not in WORD_TO_VALUE and tokens[i] not in MULTIPLIERS:
            i += 1
            continue
        total, current, has_multiplier, start = 0.0, 0.0, False, i
        while i < len(tokens) and (tokens[i] in WORD_TO_VALUE or tokens[i] in MULTIPLIERS):
            tok = tokens[i]
            if tok in MULTIPLIERS:
                mult = MULTIPLIERS[tok]
                has_multiplier = True
                if mult == 100:
                    current = (current or 1) * 100
                else:
                    total += (current or 1) * mult
                    current = 0
            else:
                current += WORD_TO_VALUE[tok]
            i += 1
        value = total + current
        followed_by_value_word = i < len(tokens) and tokens[i] in VALUE_CONTEXT_WORDS
        only_ambiguous = all(t in _AMBIGUOUS for t in tokens[start:i])
        if has_multiplier or (followed_by_value_word and not (only_ambiguous and tokens[i] == "off")):
            values.append(value)
    return values


def parse_number_token(token: str) -> Optional[float]:
    """Parses a digit token such as '1,499' or '12.5'."""
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None
