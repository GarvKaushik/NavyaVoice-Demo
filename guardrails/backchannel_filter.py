"""Backchannel Filter: Distinguishes conversational affirmations from true interruptions.

In Hinglish sales conversations, listeners constantly emit passive affirmations
such as 'haan', 'hmm', 'theek hai', 'achha', 'ji'.
This filter ensures these backchannels do NOT interrupt or barge into the agent's
in-flight speech, requiring at least two substantive words or substantive speech duration.
"""

import re
from typing import Set

# Recognized passive affirmative backchannels in Hinglish & English
DEFAULT_BACKCHANNELS: Set[str] = {
    "haan",
    "han",
    "haanji",
    "haan ji",
    "hmm",
    "hm",
    "ji",
    "achha",
    "accha",
    "theek hai",
    "thik hai",
    "ok",
    "okay",
    "yes",
    "yeah",
    "right",
    "sahi",
    "bolo",
    "sun raha hoon",
}


class BackchannelFilter:
    """Filters conversational backchannel affirmations during active agent audio output."""

    def __init__(self, backchannels: Set[str] = DEFAULT_BACKCHANNELS):
        self.backchannels = {b.lower().strip() for b in backchannels}

    def is_backchannel(self, caller_text: str) -> bool:
        """Determines if the caller utterance is merely a passive backchannel.

        Returns True if the utterance should NOT interrupt the agent.
        Returns False if the utterance is a real interruption / question.
        """
        cleaned = caller_text.strip().lower()
        if not cleaned:
            return True

        # Strip non-alphanumeric punctuation
        words = re.findall(r"\b\w+\b", cleaned)
        if not words:
            return True

        # Check full utterance match against multi-word backchannels
        if cleaned in self.backchannels:
            return True

        # Filter out individual backchannel words
        substantive_words = [w for w in words if w not in self.backchannels]

        # If there are fewer than 2 non-backchannel words, treat as passive backchannel
        if len(substantive_words) < 2:
            return True

        return False

