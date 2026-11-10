"""Per-call registry of values the agent is allowed to speak.

Every number, restricted term, date and commitment a tool returns (or an
approved playbook / policy / stage prompt contains) is registered here. The
output guard rejects anything that is not.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, Set

from speech.numbers import extract_word_numbers, parse_number_token

MONTH_WORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec",
}
WEEKDAY_WORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "somvar", "mangalvar", "budhvar", "guruvar", "shukravar", "shanivar", "ravivar",
}
DATE_WORDS = MONTH_WORDS | WEEKDAY_WORDS

NUMERIC_PATTERN = re.compile(r"(?<![\w.])\d+(?:,\d+)*(?:\.\d+)?(?![\d])")

# Restricted marketing claims (plan list plus common Hinglish variants)
RESTRICTED_TERMS = {
    "free", "free of cost", "muft", "mufat", "guarantee", "guaranteed", "guaranteed returns",
    "gaurantee", "garantee", "discount", "lifetime", "zindagi bhar", "no charges", "no charge",
    "koi charge nahi", "zero cost", "best in india", "india ka best", "number one", "no. 1",
    "100% refund", "money-back", "money back", "cashback",
}


def extract_numbers(text: str) -> Set[float]:
    """All numeric values in text, from digits and from spelled-out words."""
    values: Set[float] = set()
    for token in NUMERIC_PATTERN.findall(text):
        value = parse_number_token(token)
        if value is not None:
            values.add(value)
    values.update(extract_word_numbers(text))
    return values


def find_restricted_terms(text: str) -> Set[str]:
    lowered = text.lower()
    found = set()
    for term in RESTRICTED_TERMS:
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered):
            found.add(term)
    return found


def find_date_words(text: str) -> Set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w in DATE_WORDS}


@dataclass
class AuthorizationContext:
    """Values authorized for speech on one call."""

    numbers: Set[float] = field(default_factory=set)
    terms: Set[str] = field(default_factory=set)
    date_words: Set[str] = field(default_factory=set)
    completed_actions: Set[str] = field(default_factory=set)

    def authorize_numbers(self, values: Iterable[float]) -> None:
        for v in values:
            if v is not None:
                self.numbers.add(float(v))

    def authorize_terms(self, terms: Iterable[str]) -> None:
        self.terms.update(t.lower() for t in terms)

    def authorize_text(self, text: str) -> None:
        """Registers everything an approved text (tool phrasing, playbook, policy) contains."""
        if not text:
            return
        self.authorize_numbers(extract_numbers(text))
        self.authorize_terms(find_restricted_terms(text))
        self.date_words.update(find_date_words(text))

    def mark_action(self, action: str) -> None:
        self.completed_actions.add(action)

    def snapshot(self) -> dict:
        return {
            "numbers": sorted(self.numbers),
            "terms": sorted(self.terms),
            "date_words": sorted(self.date_words),
            "completed_actions": sorted(self.completed_actions),
        }
