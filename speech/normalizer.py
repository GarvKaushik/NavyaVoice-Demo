"""Hinglish Text Normalizer for low-latency Bulbul v4 Flash TTS.

Converts numerical quantities, currency amounts (₹), percentages (%),
dates, telephone numbers, and technical abbreviations into phonetic
Hinglish text to prevent TTS pronunciation artifacts.
"""

import re
from typing import Dict

from speech.numbers import int_to_hindi

MONTHS: Dict[str, str] = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April", "may": "May", "jun": "June",
    "jul": "July", "aug": "August", "sep": "September", "sept": "September", "oct": "October",
    "nov": "November", "dec": "December",
}

# Plan and product names spoken the way they pronounce best
PRODUCT_LEXICON: Dict[str, str] = {
    r"\bCRM-PRO\b": "C R M Pro",
    r"\bCRM-STARTER\b": "C R M Starter",
    r"\bNavyaNexus\b": "Navya Nexus",
}


class HinglishNormalizer:
    """Normalizes raw model text into phonetically consistent Hinglish."""

    @classmethod
    def int_to_hindi_phonetic(cls, num: int) -> str:
        """Converts an integer to romanized Hindi words up to 99 crores."""
        return int_to_hindi(num)

    @classmethod
    def normalize_currency(cls, text: str) -> str:
        """Converts currency expressions like '₹1,499' or 'Rs. 500' to spoken Hinglish."""
        currency_pattern = re.compile(r'(?:₹|\bRs\.?|\bINR)\s*([0-9,]+(?:\.[0-9]{1,2})?)(?:\s*/-)?', re.IGNORECASE)

        def _replace_currency(match: re.Match) -> str:
            raw_val = match.group(1).replace(",", "")
            try:
                val = float(raw_val)
            except ValueError:
                return match.group(0)
            rupees = int(val)
            paise = int(round((val - rupees) * 100))
            spoken = f"{cls.int_to_hindi_phonetic(rupees)} rupaye"
            if paise:
                spoken += f" {cls.int_to_hindi_phonetic(paise)} paise"
            return spoken

        return currency_pattern.sub(_replace_currency, text)

    @classmethod
    def normalize_percentages(cls, text: str) -> str:
        """Converts '20%' or '50 %' to 'bees percent'."""
        percent_pattern = re.compile(r'(\d+)\s*%')
        return percent_pattern.sub(lambda m: f"{cls.int_to_hindi_phonetic(int(m.group(1)))} percent", text)

    @classmethod
    def normalize_dates(cls, text: str) -> str:
        """Converts '12/11/2026', '12 Nov' or 'Nov 12' into '12 November' spoken in Hindi."""
        def _day_month(day: int, month: str) -> str:
            return f"{cls.int_to_hindi_phonetic(day)} {month}"

        def _numeric(m: re.Match) -> str:
            day, month = int(m.group(1)), int(m.group(2))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                return m.group(0)
            name = list(dict.fromkeys(MONTHS.values()))[month - 1]
            return _day_month(day, name)

        # "12/11" or "12/11/2026" or "12-11-2026"; a bare "4-5" is a range, not a date
        text = re.sub(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b", _numeric, text)
        text = re.sub(r"\b(\d{1,2})-(\d{1,2})-\d{2,4}\b", _numeric, text)

        month_re = "|".join(sorted(MONTHS.keys(), key=len, reverse=True))
        text = re.sub(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_re})[a-z]*\.?",
            lambda m: _day_month(int(m.group(1)), MONTHS[m.group(2).lower()]),
            text, flags=re.IGNORECASE,
        )
        text = re.sub(
            rf"\b({month_re})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
            lambda m: _day_month(int(m.group(2)), MONTHS[m.group(1).lower()]),
            text, flags=re.IGNORECASE,
        )
        return text

    @classmethod
    def normalize_times(cls, text: str) -> str:
        """Converts '5 baje', '5:30 PM' or '17:00' to spoken Hindi time."""
        def _clock(m: re.Match) -> str:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            suffix = (m.group(3) or "").lower()
            if suffix == "pm" and hour < 12:
                hour += 12
            if hour > 23 or minute > 59:
                return m.group(0)
            part = "subah" if hour < 12 else "dopahar" if hour < 16 else "shaam" if hour < 20 else "raat"
            h12 = hour % 12 or 12
            if minute == 30:
                spoken = f"saadhe {cls.int_to_hindi_phonetic(h12)} baje"
            elif minute == 0:
                spoken = f"{cls.int_to_hindi_phonetic(h12)} baje"
            else:
                spoken = f"{cls.int_to_hindi_phonetic(h12)} bajkar {cls.int_to_hindi_phonetic(minute)} minute"
            return f"{part} {spoken}"

        text = re.sub(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", _clock, text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d{1,2})()\s*(am|pm)\b", _clock, text, flags=re.IGNORECASE)
        text = re.sub(
            r"\b(\d{1,2})\s+baje\b",
            lambda m: f"{cls.int_to_hindi_phonetic(int(m.group(1)))} baje",
            text, flags=re.IGNORECASE,
        )
        return text

    @classmethod
    def normalize_abbreviations(cls, text: str) -> str:
        """Expands technical acronyms to avoid unnatural TTS synthesis."""
        for pattern, replacement in PRODUCT_LEXICON.items():
            text = re.sub(pattern, replacement, text)
        acronyms = {
            r'\bCRM\b': "C R M",
            r'\bAI\b': "A I",
            r'\bSMS\b': "S M S",
            r'\bAPI\b': "A P I",
            r'\bKYC\b': "K Y C",
            r'\bOTP\b': "O T P",
            r'\bURL\b': "link",
            r'\bFAQ\b': "F A Q",
            r'\bSKU\b': "product",
            r'\bDND\b': "D N D",
            r'\bEMI\b': "E M I",
            r'\bGST\b': "G S T",
        }
        for pattern, replacement in acronyms.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    @classmethod
    def normalize_phone_numbers(cls, text: str) -> str:
        """Formats 10-digit Indian numbers into paired spoken digits."""
        phone_pattern = re.compile(r'(?:\+91[\s-]?)?\b([6-9]\d{9})\b')

        def _replace_phone(match: re.Match) -> str:
            digits = match.group(1)
            part1 = " ".join(cls.int_to_hindi_phonetic(int(d)) for d in digits[:5])
            part2 = " ".join(cls.int_to_hindi_phonetic(int(d)) for d in digits[5:])
            return f"{part1}, {part2}"

        return phone_pattern.sub(_replace_phone, text)

    @classmethod
    def normalize_plain_numbers(cls, text: str) -> str:
        """Speaks any remaining digit groups (e.g. '20 calls', '2 minute') in Hindi."""
        return re.sub(
            r"\b\d{1,9}\b",
            lambda m: cls.int_to_hindi_phonetic(int(m.group(0))),
            text,
        )

    @classmethod
    def normalize(cls, raw_text: str) -> str:
        """Executes full normalization pipeline on raw text prior to TTS transmission."""
        if not raw_text:
            return ""

        text = raw_text.strip()
        text = cls.normalize_phone_numbers(text)
        text = cls.normalize_currency(text)
        text = cls.normalize_percentages(text)
        text = cls.normalize_dates(text)
        text = cls.normalize_times(text)
        text = cls.normalize_abbreviations(text)
        text = re.sub(r"(\d)-(\w)", r"\1 \2", text)  # "7-day" -> "7 day"
        text = cls.normalize_plain_numbers(text)

        text = re.sub(r'\s+', ' ', text)
        return text.strip()
