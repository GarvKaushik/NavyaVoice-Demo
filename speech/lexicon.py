"""Post-STT lexicon pass: corrects product, plan and brand names STT commonly mishears."""

import re
from typing import Dict, List

# misheard (regex, case-insensitive) -> canonical form
DEFAULT_LEXICON: Dict[str, str] = {
    r"navya\s*nexus\s*a\.?\s*i\.?": "NavyaNexus AI",
    r"navya\s*nexus|navia\s*nexus|naveya\s*nexus|navyanexus": "NavyaNexus",
    r"c\.?\s*r\.?\s*m\.?\s*pro": "CRM Pro",
    r"c\.?\s*r\.?\s*m\.?\s*starter": "CRM Starter",
    r"diwali\s*offer": "Diwali offer",
    r"\bd\.?\s*n\.?\s*d\b": "DND",
    r"what'?s\s*app|whats\s*up|watsapp|vatsapp": "WhatsApp",
}

_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in DEFAULT_LEXICON.items()]

# Terms boosted in the STT (keyterms) so product names are recognised in the first place
KEYTERMS: List[str] = ["NavyaNexus", "CRM Pro", "CRM Starter", "WhatsApp", "demo", "payment link", "DND"]


def apply_lexicon(text: str) -> str:
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text
