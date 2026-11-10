"""Sales stages state machine and stage-aware conversation guidance.

Code owns the sales stage; the LLM only writes the words. Each caller turn is
reduced to TurnSignals (fast keyword pass, refined later by the observer) and
the StageManager applies the plan's "moves on when" rules, including moving
backward (a new need during the pitch returns to discovery).
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class SalesStage(str, Enum):
    """Sales lifecycle stages as defined in the infrastructure specification."""
    OPENING = "OPENING"
    DISCOVERY = "DISCOVERY"
    NEED_IDENTIFICATION = "NEED_IDENTIFICATION"
    PITCH = "PITCH"
    OBJECTION_HANDLING = "OBJECTION_HANDLING"
    CLOSE_NEXT_STEP = "CLOSE_NEXT_STEP"
    COMPLETED = "COMPLETED"


def _has(text: str, words) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)


AFFIRMATIVE = ["haan", "han", "haanji", "ha", "ji", "ji haan", "yes", "yeah", "sahi", "bilkul", "theek hai", "thik hai",
               "correct", "right", "ok", "okay", "achha", "accha", "zaroor", "sure", "haan ji", "boliye", "bolo",
               "bataiye", "batao", "chalega"]
NEGATIVE = ["nahi", "nahin", "no", "na", "galat", "not really", "aisa nahi", "mat"]
PERMISSION_GRANT = ["boliye", "bolo", "bataiye", "batao", "kya baat hai", "haan bolo", "haan boliye", "sun raha",
                    "sun rahi", "go ahead", "tell me", "haan bataiye", "kahiye"]
PERMISSION_DENY = ["time nahi hai", "interest nahi", "interested nahi", "nahi chahiye", "mat karo", "not interested",
                   "zaroorat nahi"]
PRICE_ASK = ["price", "kitne ka", "kitna", "kitni", "cost", "charges", "charge", "fees", "fee", "rate", "paisa",
             "kimat", "keemat", "pricing", "plan kya"]
PITCH_ASK = ["kya hai ye", "kya hai yeh", "product kya", "kya karta", "features", "kaise kaam", "kya milega",
             "batao kya", "details", "explain"]
BUYING = ["link bhej", "payment", "kaise kharid", "kharidna", "le lete", "le lunga", "le lungi", "interested hoon",
          "demo", "sign up", "signup", "start karna", "shuru karna", "chahiye mujhe", "kaise lein", "subscribe", "buy"]
OBJECTIONS: Dict[str, List[str]] = {
    "price": ["mehenga", "mehnga", "expensive", "costly", "budget nahi", "zyada hai", "jyada hai", "sasta", "kam karo",
              "afford", "paise nahi"],
    "trust": ["bharosa", "trust", "genuine", "safe hai", "data safe", "fake", "kaise maan", "guarantee kya"],
    "competitor": ["pehle se", "already use", "dusra software", "doosra software", "zoho", "salesforce", "hubspot",
                   "leadsquared", "freshworks", "excel se kaam chal"],
    "need": ["zaroorat nahi", "need nahi", "kaam nahi aayega", "manual theek hai", "chhoti team", "use nahi"],
    "timing": ["abhi nahi", "baad mein dekhenge", "next month", "agle mahine", "sochna padega", "soch ke batata",
               "soch ke bataunga"],
}
NEED_PATTERNS: Dict[str, List[str]] = {
    "manual calling takes too much time": ["manual", "manually", "time lagta", "samay lagta", "haath se"],
    "leads get missed or not followed up": ["leads miss", "lead miss", "follow up", "followup", "bhool jaate", "chhoot"],
    "no tracking of calls and pipeline": ["track nahi", "tracking", "record nahi", "pata nahi chalta", "excel"],
    "slow response to new leads": ["response slow", "late call", "der se call", "time pe call nahi"],
    "team capacity is limited": ["staff kam", "team chhoti", "log kam", "banda nahi", "hiring"],
}
SMALL_TALK = ["mausam", "weather", "cricket", "match", "khana kha", "aap kahan se", "kahan se bol", "aap kaise ho",
              "aap kaisi ho", "aapka naam", "shaadi", "chhutti", "holiday", "traffic"]


@dataclass
class TurnSignals:
    affirmative: bool = False
    negative: bool = False
    question: bool = False
    permission_granted: bool = False
    permission_denied: bool = False
    asks_price: bool = False
    asks_pitch: bool = False
    buying_signal: bool = False
    objection: Optional[str] = None
    needs: List[str] = field(default_factory=list)
    small_talk: bool = False


def extract_signals(text: str) -> TurnSignals:
    """Fast keyword pass over a caller turn (the observer refines it asynchronously)."""
    t = text.lower().strip()
    sig = TurnSignals()
    sig.question = "?" in t or _has(t, ["kya", "kaise", "kitna", "kab", "kaun", "kyun", "what", "how", "why"])
    sig.permission_denied = _has(t, PERMISSION_DENY)
    sig.negative = sig.permission_denied or (_has(t, NEGATIVE) and not _has(t, ["nahi to", "kyun nahi"]))
    sig.affirmative = _has(t, AFFIRMATIVE) and not sig.negative
    sig.permission_granted = _has(t, PERMISSION_GRANT) and not sig.permission_denied
    sig.asks_price = _has(t, PRICE_ASK)
    sig.asks_pitch = _has(t, PITCH_ASK)
    sig.buying_signal = _has(t, BUYING)
    for kind, words in OBJECTIONS.items():
        if _has(t, words):
            sig.objection = kind
            break
    sig.needs = [need for need, words in NEED_PATTERNS.items() if _has(t, words)]
    sig.small_talk = _has(t, SMALL_TALK) and not (sig.asks_price or sig.buying_signal or sig.needs or sig.objection)
    return sig


class StageManager:
    """Manages stage transitions, small-talk counters, and stage-aware endpointing delays."""

    STAGE_ORDER: List[SalesStage] = [
        SalesStage.OPENING,
        SalesStage.DISCOVERY,
        SalesStage.NEED_IDENTIFICATION,
        SalesStage.PITCH,
        SalesStage.OBJECTION_HANDLING,
        SalesStage.CLOSE_NEXT_STEP,
    ]

    def __init__(self, initial_stage: SalesStage = SalesStage.OPENING):
        self.current_stage: SalesStage = initial_stage
        self.stage_before_objection: SalesStage = SalesStage.PITCH
        self.small_talk_count: int = 0
        self.confirmed_needs: List[str] = []
        self.handled_objections: Dict[str, int] = {}
        self.identity_confirmed: bool = False
        self.permission_granted: bool = False
        self.last_agent_question_closed: Optional[bool] = None
        self.history: List[Dict[str, str]] = []

    @property
    def endpointing_delay_ms(self) -> int:
        """Stage-aware endpointing delay.

        About 300 ms after a yes/no question, about 600 ms after open questions
        (Hindi speakers pause mid-thought).
        """
        if self.last_agent_question_closed is not None:
            return 300 if self.last_agent_question_closed else 600
        open_stages = (SalesStage.DISCOVERY, SalesStage.PITCH, SalesStage.OBJECTION_HANDLING)
        return 600 if self.current_stage in open_stages else 300

    def note_agent_reply(self, reply: str) -> None:
        """Remembers whether the agent just asked a yes/no question."""
        t = reply.lower()
        open_q = _has(t, ["kaise", "kitna", "kitne", "kaun", "kya kya", "kab", "kyun", "batayiye", "bataiye", "how",
                          "what", "which"])
        self.last_agent_question_closed = not open_q

    def on_stage_change(self) -> None:
        self.last_agent_question_closed = None

    def record_turn(self, is_small_talk: bool = False) -> None:
        """Tracks conversational divergence."""
        if is_small_talk:
            self.small_talk_count += 1
        else:
            self.small_talk_count = 0

    def should_bridge_back(self) -> bool:
        """Determines if the agent must politely steer away from off-topic chatter."""
        return self.small_talk_count >= 2

    def transition_to(self, next_stage: SalesStage, reason: str = "") -> None:
        """Executes stage transition forwards or backwards."""
        if next_stage == self.current_stage:
            return
        if next_stage == SalesStage.OBJECTION_HANDLING and self.current_stage != SalesStage.OBJECTION_HANDLING:
            self.stage_before_objection = self.current_stage
        self.history.append({"from": self.current_stage.value, "to": next_stage.value, "reason": reason})
        self.current_stage = next_stage
        self.small_talk_count = 0

    def apply(self, sig: TurnSignals) -> Optional[str]:
        """Applies the plan's transition rules. Returns a reason when the stage changed."""
        before = self.current_stage
        self.record_turn(sig.small_talk)
        new_needs = [n for n in sig.needs if n not in self.confirmed_needs]
        self.confirmed_needs.extend(new_needs)
        stage = self.current_stage

        if stage == SalesStage.OPENING:
            if sig.permission_denied:
                return None  # engine closes politely / offers callback
            if sig.permission_granted or sig.asks_price or sig.asks_pitch or sig.needs:
                self.permission_granted = True
                self.transition_to(SalesStage.DISCOVERY, "Permission given")
            elif sig.affirmative:
                if self.identity_confirmed:
                    self.permission_granted = True
                    self.transition_to(SalesStage.DISCOVERY, "Permission given")
                else:
                    self.identity_confirmed = True
            return self._changed(before, "Permission given")

        if sig.objection and stage != SalesStage.OPENING:
            count = self.handled_objections.get(sig.objection, 0) + 1
            self.handled_objections[sig.objection] = count
            if count >= 2:
                self.transition_to(SalesStage.CLOSE_NEXT_STEP, f"Second '{sig.objection}' objection: offer next step")
            else:
                self.transition_to(SalesStage.OBJECTION_HANDLING, f"Objection: {sig.objection}")
            return self._changed(before, "objection")

        if sig.buying_signal and stage != SalesStage.COMPLETED:
            self.transition_to(SalesStage.CLOSE_NEXT_STEP, "Buying signal")
            return self._changed(before, "buying signal")

        if stage == SalesStage.DISCOVERY:
            if len(self.confirmed_needs) >= 2:
                self.transition_to(SalesStage.NEED_IDENTIFICATION, "2+ needs captured")
            elif sig.asks_price or sig.asks_pitch:
                nxt = SalesStage.NEED_IDENTIFICATION if self.confirmed_needs else SalesStage.PITCH
                self.transition_to(nxt, "Customer pushed for the pitch")
        elif stage == SalesStage.NEED_IDENTIFICATION:
            if sig.affirmative or sig.asks_price or sig.asks_pitch:
                self.transition_to(SalesStage.PITCH, "Customer agreed with the summary")
            elif sig.negative:
                self.transition_to(SalesStage.DISCOVERY, "Summary rejected; rediscover")
        elif stage == SalesStage.PITCH:
            if new_needs:
                self.transition_to(SalesStage.DISCOVERY, "New need raised during pitch")
        elif stage == SalesStage.OBJECTION_HANDLING:
            if sig.affirmative or sig.asks_price or sig.question:
                back = self.stage_before_objection
                if back in (SalesStage.OPENING, SalesStage.OBJECTION_HANDLING):
                    back = SalesStage.PITCH
                self.transition_to(back, "Objection resolved")
        return self._changed(before, self.history[-1]["reason"] if self.history else "")

    def _changed(self, before: SalesStage, reason: str) -> Optional[str]:
        return reason if self.current_stage != before else None

    def get_stage_guidance(self) -> str:
        """Brief goal for the turn prompt."""
        guidance = {
            SalesStage.OPENING: "Goal: Confirm right person, then ask for 2 minutes of their time.",
            SalesStage.DISCOVERY: "Goal: Learn their situation, current solution and pain points. One question.",
            SalesStage.NEED_IDENTIFICATION: "Goal: State the customer's main need in one short sentence and ask if correct.",
            SalesStage.PITCH: "Goal: Match 1-2 features to the confirmed need. Keep it under 25 words.",
            SalesStage.OBJECTION_HANDLING: "Goal: Answer with the approved playbook for this objection. Never argue.",
            SalesStage.CLOSE_NEXT_STEP: "Goal: Offer one clear next step: payment link, demo booking, expert or callback.",
            SalesStage.COMPLETED: "Goal: The action is confirmed. Thank them and close in one sentence.",
        }
        return guidance.get(self.current_stage, "")
