"""Client-facing call conclusions. Derived from the recorded outcome, never from a guess."""

READY = {"interested", "payment_link_sent", "demo_booked"}
REFUSED = {"not_interested", "opted_out"}
CONTACT_AGAIN = {"callback_scheduled", "callback_requested", "busy", "no_answer"}
MANAGER = {"transferred", "manager_callback"}
UNCLEAR = {"unclear", "completed"}

CONCLUSIONS = ("ready", "refused", "contact_again", "manager", "unclear", "other")
CONCLUSION_LABELS = {
    "ready": "Ready",
    "refused": "Refused",
    "contact_again": "Contact again",
    "manager": "Talk to manager",
    "unclear": "Unclear",
    "other": "Not reached / other",
}
_GROUPS = {"ready": READY, "refused": REFUSED, "contact_again": CONTACT_AGAIN, "manager": MANAGER, "unclear": UNCLEAR}


def conclusion_for(outcome: str) -> str:
    for conclusion, outcomes in _GROUPS.items():
        if outcome in outcomes:
            return conclusion
    return "other"


def outcomes_for(conclusion: str) -> set:
    return set(_GROUPS.get(conclusion, set()))
