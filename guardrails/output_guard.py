"""Output Guard: Zero-tolerance deterministic validation between LLM and TTS.

LLM output streams into a sentence buffer; each complete sentence is checked
before release to TTS:
1. Every number, ₹ amount and percentage (digits or words) must match a value
   returned by a tool, or contained in approved text, on this call.
2. Dates (month and weekday names) must come from a tool result.
3. Restricted terms pass only when a tool result or approved playbook contains them.
4. Commitments (link sent, demo booked, transfer, callback, refund, activation)
   are spoken only after the matching tool call succeeded.
A failing sentence is replaced with a safe line and the event is logged.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple

from guardrails.authorization import (
    AuthorizationContext,
    extract_numbers,
    find_date_words,
    find_restricted_terms,
)

logger = logging.getLogger(__name__)


@dataclass
class GuardVerificationResult:
    """Outcome of sentence verification by the Output Guard."""
    passed: bool
    sanitized_sentence: str
    original_sentence: str
    reasons: List[str] = field(default_factory=list)
    rules: List[str] = field(default_factory=list)
    replaced_with_safe: bool = False


# (pattern, required completed action, rule name)
COMMITMENT_RULES: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"(link|payment).{0,40}\b(bhej|send|sent)\s*(diya|di|de\s*diya|kar\s*diya|kar\s*di|chuki|chuka|ho\s*gaya|ho\s*gayi|raha|rahi|rahe)\b|\b(bhej|send)\s*(diya|di)\b.{0,30}\blink", re.I), "send_payment_link", "commitment_payment_link"),
    (re.compile(r"(demo|meeting|slot|appointment).{0,40}\b(book|confirm|schedule|fix)\w*\s*(ho\s*gaya|ho\s*gayi|kar\s*(diya|di|li|liya|deti|dungi|rahi|raha)|karti|ho\s*jayega|hai)\b", re.I), "book_demo", "commitment_demo"),
    (re.compile(r"\b(transfer|connect)\s*kar\s*(rahi|raha|rahe|deti|deta|dungi|dunga|di|diya)\b", re.I), "transfer_to_human", "commitment_transfer"),
    (re.compile(r"\b(aapko|aap\s*ko)\b.{0,40}\b(call|phone)\s*(karungi|karunga|karti\s*hoon|karta\s*hoon|karenge|karegi|karte\s*hain|kar\s*dungi)\b", re.I), "schedule_callback", "commitment_callback"),
    (re.compile(r"\brefund\b.{0,50}\b(mil\s*jayega|ho\s*jayega|milega|kar\s*denge|de\s*denge|mil\s*jaata)\b", re.I), "policy:refunds", "commitment_refund"),
    (re.compile(r"\b(deliver\w*|activat\w*|onboard\w*|install\w*|setup|set\s*up|account)\b.{0,50}\b(ho\s*jayega|ho\s*jayegi|kar\s*denge|mil\s*jayega|ho\s*jaata|shuru\s*ho\s*jayega)\b", re.I), "check_availability", "commitment_activation"),
    (re.compile(r"\b(number|list)\b.{0,30}\b(hata\s*(diya|di)|remove\s*kar\s*(diya|di)|nikal\s*(diya|di))\b", re.I), "record_opt_out", "commitment_opt_out"),
]

# Small conversational quantities ("bas 2 minute") that carry no commercial meaning
_SMALL_DURATION = re.compile(r"\b([1-5])\s*(minute|minutes|min|mins|second|seconds|sec|secs)\b", re.I)


class OutputGuard:
    """Inspects generated LLM sentences before they are synthesized into speech."""

    SAFE_FALLBACK = "Main exact detail confirm karke batati hoon."

    def __init__(self, safe_replacement: Optional[str] = None):
        self.safe_replacement = safe_replacement or self.SAFE_FALLBACK

    def extract_numbers_from_text(self, text: str) -> Set[float]:
        return extract_numbers(text)

    def verify_sentence(
        self,
        sentence: str,
        authorized_numbers: Optional[Iterable[float]] = None,
        authorized_terms: Optional[Iterable[str]] = None,
        *,
        context: Optional[AuthorizationContext] = None,
    ) -> GuardVerificationResult:
        """Verifies a single completed sentence against the call's authorization context."""
        if context is None:
            context = AuthorizationContext()
            context.authorize_numbers(authorized_numbers or [])
            context.authorize_terms(authorized_terms or [])

        reasons: List[str] = []
        rules: List[str] = []

        # 1. Numbers, prices and percentages
        allowed_small = {float(m.group(1)) for m in _SMALL_DURATION.finditer(sentence)}
        unauthorized = {
            n for n in extract_numbers(sentence)
            if n not in context.numbers and n not in allowed_small
        }
        if unauthorized:
            reasons.append(
                f"Unauthorized numeric values found in sentence: {sorted(unauthorized)}. "
                f"Allowed: {sorted(context.numbers)}"
            )
            rules.append("unauthorized_number")

        # 2. Dates
        unauthorized_dates = find_date_words(sentence) - context.date_words
        if unauthorized_dates:
            reasons.append(f"Unauthorized date reference: {sorted(unauthorized_dates)}")
            rules.append("unauthorized_date")

        # 3. Restricted marketing terms
        for term in sorted(find_restricted_terms(sentence)):
            if term not in context.terms:
                reasons.append(
                    f"Unauthorized restricted marketing claim: '{term}'. "
                    f"Not authorized in current offer/policy context."
                )
                rules.append("restricted_term")

        # 4. Commitments need a successful tool call first (questions/offers are fine)
        if not sentence.rstrip().endswith("?"):
            for pattern, action, rule in COMMITMENT_RULES:
                if pattern.search(sentence) and action not in context.completed_actions:
                    reasons.append(f"Commitment spoken before '{action}' succeeded")
                    rules.append(rule)

        if reasons:
            logger.warning(
                "OutputGuard BLOCK",
                extra={"sentence": sentence, "reasons": reasons, "rules": rules},
            )
            return GuardVerificationResult(
                passed=False,
                sanitized_sentence=self.safe_replacement,
                original_sentence=sentence,
                reasons=reasons,
                rules=rules,
                replaced_with_safe=True,
            )

        return GuardVerificationResult(
            passed=True,
            sanitized_sentence=sentence,
            original_sentence=sentence,
        )


# While streaming, a sentence ends only at punctuation followed by whitespace,
# so "₹1." + "99" arriving in two chunks is never split.
_SENTENCE_END = re.compile(r"([.?!।]+)(\s+)")
_NO_SPLIT_BEFORE = {"rs", "dr", "mr", "mrs", "ms", "no", "st", "approx", "sr"}


class SentenceBuffer:
    """Accumulates streamed LLM text and releases complete sentences.

    The first sentence of a reply is released early at a comma once it is long
    enough, so TTS can start sooner.
    """

    FIRST_SENTENCE_SOFT_LIMIT = 120  # early release only at a comma, never mid-clause
    HARD_LIMIT = 240

    def __init__(self):
        self._buf = ""
        self._released = 0

    def push(self, text: str) -> List[str]:
        self._buf += text
        out: List[str] = []
        search_from = 0
        while True:
            m = _SENTENCE_END.search(self._buf, search_from)
            if m:
                prev_word = re.findall(r"(\w+)$", self._buf[: m.start(1)])
                if m.group(1) == "." and prev_word and prev_word[0].lower() in _NO_SPLIT_BEFORE:
                    search_from = m.end()
                    continue
                out.append(self._buf[: m.end(1)].strip())
                self._buf = self._buf[m.end():]
                search_from = 0
                continue
            limit = self.FIRST_SENTENCE_SOFT_LIMIT if self._released + len(out) == 0 else self.HARD_LIMIT
            if len(self._buf) > limit:
                cut = self._buf.rfind(",", 0, limit + 1)
                if cut == -1 and len(self._buf) > self.HARD_LIMIT:
                    cut = self._buf.rfind(" ", 0, self.HARD_LIMIT + 1)
                if cut > 20:
                    out.append(self._buf[: cut + 1].strip())
                    self._buf = self._buf[cut + 1:]
                    continue
            break
        out = [s for s in out if s]
        self._released += len(out)
        return out

    def flush(self) -> List[str]:
        rest = self._buf.strip()
        self._buf = ""
        if rest:
            self._released += 1
            return [rest]
        return []
