"""Fast-Path Guard: deterministic screening of every final caller transcript.

Runs before any reply is generated (it is a sub-millisecond regex pass), and
the observer's classifier re-checks each turn in parallel with the reply. If a
critical intent (opt-out, call later, wrong person, abuse, human request,
complaint) is detected, the LLM reply is skipped or cancelled and a scripted,
pre-rendered response is played.
"""

import re
from dataclasses import dataclass
from typing import Optional

from speech.phrase_cache import PreRenderedPhraseCache


@dataclass
class FastPathResult:
    """Outcome of fast-path transcript screening."""
    triggered: bool
    intent: Optional[str] = None  # 'opt_out', 'call_later', 'wrong_person', 'abuse', 'transfer_human', 'complaint'
    action: Optional[str] = None  # 'terminate_with_dnd', 'schedule_callback', 'terminate_cleanly', 'transfer', 'log_complaint'
    response_phrase_id: Optional[str] = None
    confidence: float = 1.0


class FastPathGuard:
    """Evaluates caller turns against high-priority deterministic regex and keyword patterns."""

    OPT_OUT_PATTERNS = [
        r"\b(call|phone|fon)\s*(mat|na|nahi|band)\s*(karo|karna|karein|kijiye|kariye|kare)\b",
        r"\b(do\s*not\s*call|don'?t\s*call|dont\s*call|stop\s*call(ing)?|never\s*call)\b",
        r"(number|list|database)\s*(se)?\s*(hata|nikal|remove|delete|hatao|nikalo)",
        r"\b(remove|delete)\s*(my|mera)\s*number\b",
        r"\b(dnd\s*(me|mein|par|pe)?\s*(daal|dalo|karo|kar\s*do|laga)|register\s*dnd|dnd\s*activate)\b",
        r"\b(unsubscribe|opt\s*out)\b",
        r"\b(dubara|dobara|aage\s*se|phir\s*se|kabhi)\s*(call|phone)\s*(mat|na|nahi)\b",
        r"\b(call|phone)\s*(karna|karne)\s*band\b",
    ]

    CALL_LATER_PATTERNS = [
        r"\b(baad\s*(mein|me)\s*(call|phone|baat))",
        r"\b(abhi\s*)?(busy\s*(hoon|hu|hun)|meeting\s*(mein|me)\s*(hoon|hu|hun)|drive\s*kar\s*(raha|rahi))\b",
        r"\b(call\s*(me\s*)?back\s*later|call\s*me\s*later|call\s*later)\b",
        r"\b(kal|parso|shaam\s*ko|subah|raat\s*ko|dopahar\s*(mein|ko)?)\s*(call|phone|baat)\s*(karo|karna|kijiye|karein|kar\s*lena)\b",
        r"\babhi\s*(baat\s*)?nahi\s*kar\s*(sakta|sakti|paunga|paungi)\b",
        r"\b(\d+|ek|do|teen)\s*(ghante|minute)\s*(baad|mein)\s*(call|phone)\b",
    ]

    WRONG_PERSON_PATTERNS = [
        r"\b(wrong\s*number|galat\s*number|rong\s*number)\b",
        r"\b(main|mai)\s*(wo|woh|vo)\s*nahi\s*(hoon|hu|hun)\b",
        r"\b(ye|yeh)\s*(unka|uska)\s*number\s*nahi\b",
        r"\b(kisko\s*(lagaya|call\s*kiya)|wrong\s*person)\b",
        r"\b(is\s*naam\s*ka\s*koi\s*nahi|yahan\s*koi\s*\w+\s*nahi\s*(rehta|hai))\b",
    ]

    TRANSFER_HUMAN_PATTERNS = [
        r"\b(human|insaan|aadmi|real\s*person|asli\s*insaan)\s*(se)?\s*baat\b",
        r"\b(agent|executive|manager|senior|supervisor)\s*(ko\s*transfer|se\s*baat)\b",
        r"\b(speak|talk)\s*to\s*(a|an)?\s*(human|person|agent|executive|manager)\b",
        r"\btransfer\s*(the\s*)?call\b",
        r"\b(kisi|kisi\s*aur)\s*(senior|insaan|banda)?\s*se\s*baat\s*karao\b",
    ]

    COMPLAINT_PATTERNS = [
        r"\b(complaint|shikayat)\s*(darj|register|file|karni|karna|karunga|karungi|kar\s*raha)\b",
        r"\b(complain)\s*(karna|karunga|karungi|kar\s*raha)\b",
    ]

    ABUSE_PATTERNS = [
        r"\b(chutiya|madarchod|behenchod|bhenchod|gandu|harami|kamina)\b",
        r"\b(fraud|scam|scammer|thug|dhokebaaz|chor)\s*(ho|hai|company)\b",
        r"\bpolice\s*(ko|mein)\s*(complaint|bataunga|report)\b",
    ]

    def __init__(self):
        flags = re.IGNORECASE
        self._opt_out_re = re.compile("|".join(self.OPT_OUT_PATTERNS), flags)
        self._call_later_re = re.compile("|".join(self.CALL_LATER_PATTERNS), flags)
        self._wrong_person_re = re.compile("|".join(self.WRONG_PERSON_PATTERNS), flags)
        self._transfer_human_re = re.compile("|".join(self.TRANSFER_HUMAN_PATTERNS), flags)
        self._complaint_re = re.compile("|".join(self.COMPLAINT_PATTERNS), flags)
        self._abuse_re = re.compile("|".join(self.ABUSE_PATTERNS), flags)

    def evaluate(self, transcript: str) -> FastPathResult:
        """Evaluates incoming transcript and returns immediate intercept action if matched."""
        clean_text = transcript.strip().lower()
        if not clean_text:
            return FastPathResult(triggered=False)

        # 1. Opt-out (highest priority: legal and DND compliance)
        if self._opt_out_re.search(clean_text):
            return FastPathResult(
                triggered=True,
                intent="opt_out",
                action="terminate_with_dnd",
                response_phrase_id=PreRenderedPhraseCache.OPT_OUT_CONFIRMED,
            )

        # 2. Complaint (must be logged and the published contact given)
        if self._complaint_re.search(clean_text):
            return FastPathResult(
                triggered=True,
                intent="complaint",
                action="log_complaint",
                response_phrase_id=PreRenderedPhraseCache.COMPLAINT_LOGGED,
            )

        # 3. Abuse / hostility
        if self._abuse_re.search(clean_text):
            return FastPathResult(
                triggered=True,
                intent="abuse",
                action="terminate_cleanly",
                response_phrase_id=PreRenderedPhraseCache.ABUSE_CLOSE,
            )

        # 4. Transfer to human
        if self._transfer_human_re.search(clean_text):
            return FastPathResult(
                triggered=True,
                intent="transfer_human",
                action="transfer",
                response_phrase_id=PreRenderedPhraseCache.TRANSFER_TO_AGENT,
            )

        # 5. Wrong person
        if self._wrong_person_re.search(clean_text):
            return FastPathResult(
                triggered=True,
                intent="wrong_person",
                action="terminate_cleanly",
                response_phrase_id=PreRenderedPhraseCache.WRONG_PERSON,
            )

        # 6. Call later / busy
        if self._call_later_re.search(clean_text):
            return FastPathResult(
                triggered=True,
                intent="call_later",
                action="schedule_callback",
                response_phrase_id=PreRenderedPhraseCache.ASK_CALLBACK_TIME,
            )

        return FastPathResult(triggered=False)
