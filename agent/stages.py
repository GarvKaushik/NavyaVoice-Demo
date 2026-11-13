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


has_any = _has


AFFIRMATIVE = ["haan", "han", "haanji", "ha", "ji", "ji haan", "yes", "yeah", "sahi", "bilkul", "theek hai", "thik hai",
               "correct", "right", "ok", "okay", "achha", "accha", "zaroor", "sure", "haan ji", "boliye", "bolo",
               "bataiye", "batao", "chalega"]
NEGATIVE = ["nahi", "nahin", "no", "na", "galat", "not really", "aisa nahi", "mat"]
PERMISSION_GRANT = ["boliye", "bolo", "bataiye", "batao", "kya baat hai", "haan bolo", "haan boliye", "sun raha",
                    "sun rahi", "go ahead", "tell me", "haan bataiye", "kahiye"]
PERMISSION_DENY = ["time nahi hai", "interest nahi", "interested nahi", "nahi chahiye", "mat karo", "not interested",
                   "zaroorat nahi"]
# A firm no ends the call politely; a half-meant no gets up to PERSUASION_MAX_ATTEMPTS polite tries
FIRM_NO_STRONG = ["bilkul nahi", "kabhi nahi", "koi interest nahi", "bilkul interest nahi", "no thanks", "no thank you"]
FIRM_NO = ["not interested", "interest nahi", "interested nahi", "nahi chahiye", "mat karo", "zaroorat hi nahi"]
SOFT_NO = ["abhi nahi", "abhi to nahi", "filhaal nahi", "abhi zaroorat nahi", "pata nahi", "shayad", "sochna padega",
           "soch ke", "sochenge", "dekhte hain", "time nahi", "budget nahi", "mehenga", "mehnga", "nahi lagta",
           "sure nahi", "not sure", "not now", "itna zaroori nahi"]
TIME_NO = ["time nahi", "abhi nahi", "abhi to nahi", "not now"]
PRICE_ASK = ["price", "kitne ka", "kitne", "kitna", "kitni", "cost", "charges", "charge", "fees", "fee", "rate", "paisa",
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
    "leads get missed or not followed up": ["leads miss", "lead miss", "follow up", "followup", "follow-up", "bhool jaate", "chhoot"],
    "no tracking of calls and pipeline": ["track nahi", "tracking", "record nahi", "pata nahi chalta", "excel"],
    "slow response to new leads": ["response slow", "late call", "der se call", "time pe call nahi"],
    "team capacity is limited": ["staff kam", "team chhoti", "log kam", "banda nahi", "hiring"],
}
MAX_EXPLANATIONS = 2  # after this the customer hears the price instead of more explaining
MAX_DISCOVERY_TURNS = 4  # short answers must not keep the call in discovery forever
# A direct request for the price; bare "kitna" also appears in answers like "kitni calls", so it does not count alone
PRICE_QUESTION = re.compile(r"\b(price|pricing|cost|charges?|fees?|rate|keemat|kimat)\b"
                            r"|\bkitne\s+(ka|ki|ke|mein|me|paise|rupaye)\b"
                            r"|\bkitna\s+(lagega|padega|paisa|charge|kharcha)\b")
# Words that show a refusal is about being contacted, not about the question just asked
CONTACT_WORDS = re.compile(r"\b(call|calls|phone|fon|number|contact|list|dnd|message|sms|whatsapp|disturb|pareshan\w*|dobara|dubara)\b")
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
    wants_price: bool = False
    identity_yes: bool = False
    buying_signal: bool = False
    objection: Optional[str] = None
    needs: List[str] = field(default_factory=list)
    small_talk: bool = False
    refusal: Optional[str] = None  # "firm" or "soft" (half-meant) no


def refusal_kind(text: str) -> Optional[str]:
    """Whether a turn is a firm no, a half-meant no ("abhi nahi", "sochna padega", "mehenga hai"), or neither.
    A bare "nahi" is neither: it may only answer the question just asked."""
    t = text.lower().strip()
    if _has(t, FIRM_NO_STRONG):
        return "firm"
    if _has(t, SOFT_NO):
        return "soft"
    return "firm" if _has(t, FIRM_NO) else None


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
    sig.identity_yes = _has(t, ["speaking", "bol raha", "bol rahi", "main hi", "main hoon", "haan main"]) and not sig.negative
    sig.wants_price = bool(PRICE_QUESTION.search(t)) and not sig.objection and not _has(t, ["nahi", "nahin", "mat"])
    sig.small_talk = _has(t, SMALL_TALK) and not (sig.asks_price or sig.buying_signal or sig.needs or sig.objection)
    sig.refusal = refusal_kind(t)
    return sig


def opt_out_clarity(text: str) -> str:
    """How far a turn the classifier called an opt-out can be trusted: "clear", "unclear", or "none" for a short
    answer such as "ji nahi", which only answers the question just asked."""
    t = text.lower().strip()
    if CONTACT_WORDS.search(t):
        return "clear"
    return "none" if len(t.split()) <= 4 else "unclear"


def link_consent(text: str, last_reply: str) -> bool:
    """True only when the customer asked for a payment link, or said yes to a question offering only that link."""
    t = text.lower().strip()
    if _has(t, ["nahi", "nahin", "mat", "no", "not"]):
        return False
    is_question = "?" in t or _has(t, ["kya", "kaise", "kitna", "kab", "kyun"])
    if not is_question and (_has(t, ["link", "payment", "buy", "subscribe", "le lunga", "le lungi", "le lete", "le lenge"])
                            or re.search(r"\bkharid\w*", t)):
        return True
    question = re.split(r"(?<=[.!?])\s+", last_reply.strip())[-1].lower()
    if not question.endswith("?") or not _has(question, ["link", "payment"]) or _has(question, ["ya"]):
        return False
    return extract_signals(t).affirmative or bool(re.search(r"\bbhej\w*", t))


class CallStep(str, Enum):
    """Where the call is in its fixed path. Each sales stage above can span several of these."""
    CONFIRM_PERSON = "CONFIRM_PERSON"
    HOW_ARE_YOU = "HOW_ARE_YOU"
    OFFER = "OFFER"
    OFFER_AGAIN = "OFFER_AGAIN"
    EXPLAIN = "EXPLAIN"
    PRICE = "PRICE"
    NEXT_STEP = "NEXT_STEP"
    DECLINED = "DECLINED"


@dataclass(frozen=True)
class StepPlan:
    stage: SalesStage
    line: Optional[str] = None  # spoken as written, with no model call
    line_en: Optional[str] = None  # the same line for English campaigns
    goal: str = ""  # what the model must do on this step


# The call's path: edit here to change what the agent does at each step. The order rules are in StageManager._next_step.
CALL_FLOW: Dict[CallStep, StepPlan] = {
    CallStep.CONFIRM_PERSON: StepPlan(SalesStage.OPENING, goal=(
        "The customer has not confirmed who they are. Answer anything they asked, then ask if you are speaking to "
        "{name} ji. If it is the wrong person, apologise and call end_call with wrong_person.")),
    CallStep.HOW_ARE_YOU: StepPlan(SalesStage.OPENING, line="Hello {name} ji, aap kaise hain?",
                                   line_en="Hello {name}, how are you?"),
    CallStep.OFFER: StepPlan(SalesStage.OPENING, goal=(
        "The customer is {name} and has just answered how they are. Do not ask who they are. Say, in under 30 words: "
        "(1) a warm reaction in 2-3 words, and if they asked how you are, say you are fine; (2) one line starting "
        "'Main {company} se bol rahi hoon' saying what you do, from PRODUCT INTRO, without naming the product; (3) ask if "
        "they would want a product that does this, like 'Kya aap aisa software lena chahenge jo ...?'. "
        "No price, features, or questions about how they work now.")),
    CallStep.OFFER_AGAIN: StepPlan(SalesStage.OPENING, goal=(
        "They said no to your offer. Say 'koi baat nahi', give one short benefit from PRODUCT INTRO, and ask once more "
        "if it is worth a look; if they said they have no time, offer to call later instead. Under 20 words.")),
    CallStep.EXPLAIN: StepPlan(SalesStage.PITCH, goal=(
        "The customer has never heard of the product. In up to 3 sentences and about 45 words: (1) a short first "
        "sentence naming the product and what kind of product it is, like '<product name> ek software hai jo ...'; "
        "(2) who it is for and one or two real benefits for them from PRODUCT INTRO and KEY FEATURES. If you already "
        "explained it, skip (1) and say a new benefit instead of repeating. "
        "End with a short check like 'aapke kaam ka lagta hai?'. Never ask what else they want to know.")),
    CallStep.PRICE: StepPlan(SalesStage.CLOSE_NEXT_STEP),  # code speaks the catalog price, then the model offers the next step
    CallStep.NEXT_STEP: StepPlan(SalesStage.CLOSE_NEXT_STEP),
    CallStep.DECLINED: StepPlan(SalesStage.OPENING),  # code plays the polite close and ends the call
}


# Replaces the OFFER goal when the client has written a company introduction (the INTRODUCTION script entry):
# the company comes first, and the main product is named on the next step.
OFFER_WITH_COMPANY_INTRO = (
    "The customer is {name} and has just answered how they are. Do not ask who they are. Introduce the company "
    "properly, like a person would, in 4-5 natural sentences and about 70 words. Do not cut it short: "
    "(1) a warm reaction in 2-3 words, and if they asked how you are, say you are fine; (2) 'Main {company} se bol "
    "rahi hoon', then what the company does in its industry and the kinds of services it provides, from COMPANY "
    "INTRO; (3) the company's main goal for its customers, from COMPANY INTRO; (4) ask for two minutes to tell them "
    "about a service, like 'Kya main aapke do minute le sakti hoon, ek service ke baare mein batane ke liye jo aapke "
    "business ke kaam aa sakti hai?' (never 'do minute de sakti hoon'). Use only what COMPANY INTRO says: no invented clients, numbers or claims. Do not name the product yet. "
    "No price or features.")


# The first product turn after a company introduction: lead from the company to the product
PRODUCT_BRIDGE = (
    "You introduced the company and they gave you two minutes. Your FIRST sentence must lead from the company to the "
    "product, in your own words, like 'Main aapko {product} ke baare mein batane ke liye call kar rahi thi, jo ek ... "
    "hai'. Then: ")


# English campaigns: the goals' quoted Hinglish example lines, in English (models copy quoted text word for word)
ENGLISH_QUOTES = {
    "{name} ji": "{name}",
    "'Main {company} se bol rahi hoon'": "'I am calling from {company}'",
    "'Kya aap aisa software lena chahenge jo ...?'": "'Would you be interested in software that ...?'",
    "'koi baat nahi'": "'no problem'",
    "'<product name> ek software hai jo ...'": "'<product name> is software that ...'",
    "'aapke kaam ka lagta hai?'": "'does this sound useful for you?'",
    "'Kya main aapke do minute le sakti hoon, ek service ke baare mein batane ke liye jo aapke business ke kaam aa "
    "sakti hai?' (never 'do minute de sakti hoon')": "'Could I take two minutes to tell you about a service that could "
                                                     "help your business?'",
    "'Main aapko {product} ke baare mein batane ke liye call kar rahi thi, jo ek ... hai'":
        "'I was calling to tell you about {product}, which is ...'",
}


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

    def __init__(self, initial_stage: SalesStage = SalesStage.OPENING, max_persuasion: int = 2):
        self.current_stage: SalesStage = initial_stage
        self.max_persuasion = max_persuasion
        self.persuasion_attempts: int = 0
        self.persuading: bool = False  # this turn answers a half-meant no
        self.stage_before_objection: SalesStage = SalesStage.PITCH
        self.small_talk_count: int = 0
        self.confirmed_needs: List[str] = []
        self.discovery_turns: int = 0
        self.handled_objections: Dict[str, int] = {}
        self.step: CallStep = CallStep.EXPLAIN if initial_stage == SalesStage.PITCH else CallStep.CONFIRM_PERSON
        self.explanations: int = 0
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
        if self.current_stage == SalesStage.PITCH:
            self.explanations += 1
        elif self.step == CallStep.PRICE:
            self.step = CallStep.NEXT_STEP

    def note_known_needs(self, needs: List[str]) -> None:
        """Adds needs the observer found on earlier turns, so they count towards moving on."""
        self.confirmed_needs.extend(n for n in needs if n not in self.confirmed_needs)

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
        self.discovery_turns = 0
        self.small_talk_count = 0

    def _next_step(self, sig: TurnSignals) -> CallStep:
        """The order of the call: what the customer's answer to the current step leads to."""
        if self.step == CallStep.CONFIRM_PERSON:
            confirmed = sig.affirmative or sig.permission_granted or sig.identity_yes
            return CallStep.HOW_ARE_YOU if confirmed else CallStep.CONFIRM_PERSON
        if self.step == CallStep.HOW_ARE_YOU:
            return CallStep.OFFER
        if self.step == CallStep.OFFER:
            if not sig.negative:
                return CallStep.EXPLAIN
            if sig.refusal == "firm" or self.persuasion_attempts >= self.max_persuasion:
                return CallStep.DECLINED
            self.persuasion_attempts += 1
            return CallStep.OFFER_AGAIN
        if self.step == CallStep.OFFER_AGAIN:
            if not sig.negative:
                return CallStep.EXPLAIN
            if sig.refusal == "soft" and self.persuasion_attempts < self.max_persuasion:
                self.persuasion_attempts += 1
                return CallStep.OFFER_AGAIN  # a half-meant no gets one more polite try
            return CallStep.DECLINED
        if self.step == CallStep.EXPLAIN and not sig.negative:
            wants_more = sig.question or sig.asks_pitch
            if self.explanations >= MAX_EXPLANATIONS or (self.explanations and not wants_more):
                return CallStep.PRICE
        return self.step

    def goal_for_step(self, name: str, company: str, company_intro: bool = False, product: str = "",
                      english: bool = False) -> str:
        """The model's instruction for the current step, while the call is on its fixed path."""
        plan = CALL_FLOW[self.step]
        if plan.stage != self.current_stage:
            return ""
        goal = OFFER_WITH_COMPANY_INTRO if company_intro and self.step == CallStep.OFFER else plan.goal
        if company_intro and product and self.step == CallStep.EXPLAIN and not self.explanations:
            goal = PRODUCT_BRIDGE + goal
        if english:
            for hinglish, en in ENGLISH_QUOTES.items():
                goal = goal.replace(hinglish, en)
        return goal.format(name=name, company=company, product=product)

    def is_company_intro_turn(self, company_intro: bool) -> bool:
        return company_intro and self.current_stage == SalesStage.OPENING and self.step == CallStep.OFFER

    def is_long_turn(self, company_intro: bool = False) -> bool:
        """Turns that explain something new get more words: the pitch, and the company introduction."""
        return self.current_stage == SalesStage.PITCH or self.is_company_intro_turn(company_intro)

    def line_for_step(self, name: str, english: bool = False) -> Optional[str]:
        plan = CALL_FLOW[self.step]
        line = plan.line_en if english and plan.line_en else plan.line
        return line.format(name=name) if line and plan.stage == self.current_stage else None

    def apply(self, sig: TurnSignals) -> Optional[str]:
        """Applies the plan's transition rules. Returns a reason when the stage changed."""
        before = self.current_stage
        self.persuading = False
        self.record_turn(sig.small_talk)
        new_needs = [n for n in sig.needs if n not in self.confirmed_needs]
        self.confirmed_needs.extend(new_needs)
        stage = self.current_stage

        if stage == SalesStage.OPENING:
            if sig.permission_denied and sig.refusal != "soft":
                return None  # engine closes politely / offers callback
            if sig.wants_price:
                self.step = CallStep.PRICE
            elif sig.buying_signal:
                self.step = CallStep.NEXT_STEP
            elif sig.asks_price or sig.asks_pitch or sig.needs:
                self.step = CallStep.EXPLAIN
            else:
                self.step = self._next_step(sig)
            self.transition_to(CALL_FLOW[self.step].stage, f"Call step: {self.step.value}")
            return self._changed(before, f"Call step: {self.step.value}")

        if sig.refusal and stage != SalesStage.COMPLETED and not (sig.question or sig.asks_price or sig.asks_pitch
                                                                 or sig.buying_signal):
            if sig.refusal == "firm" or self.persuasion_attempts >= self.max_persuasion:
                self.step = CallStep.DECLINED  # engine closes politely
                return None
            self.persuasion_attempts += 1
            self.persuading = True

        if sig.objection and stage != SalesStage.OPENING:
            count = self.handled_objections.get(sig.objection, 0) + 1
            self.handled_objections[sig.objection] = count
            if count >= 2:
                self.transition_to(SalesStage.CLOSE_NEXT_STEP, f"Second '{sig.objection}' objection: offer next step")
            else:
                self.transition_to(SalesStage.OBJECTION_HANDLING, f"Objection: {sig.objection}")
            return self._changed(before, "objection")

        if sig.buying_signal and stage != SalesStage.COMPLETED:
            if stage == SalesStage.PITCH and self.explanations:
                sig.wants_price = True  # they have heard what it does, so the next thing they hear is the price
            self.step = CallStep.PRICE if sig.wants_price else CallStep.NEXT_STEP
            self.transition_to(SalesStage.CLOSE_NEXT_STEP, "Buying signal")
            return self._changed(before, "buying signal")

        if stage == SalesStage.DISCOVERY:
            self.discovery_turns += 1
            nxt = SalesStage.NEED_IDENTIFICATION if self.confirmed_needs else SalesStage.PITCH
            if len(self.confirmed_needs) >= 2:
                self.transition_to(SalesStage.NEED_IDENTIFICATION, "2+ needs captured")
            elif sig.asks_price or sig.asks_pitch:
                self.transition_to(nxt, "Customer pushed for the pitch")
            elif self.discovery_turns >= MAX_DISCOVERY_TURNS:
                self.transition_to(nxt, "Discovery has run long enough")
        elif stage == SalesStage.NEED_IDENTIFICATION:
            if sig.affirmative or sig.asks_price or sig.asks_pitch:
                self.transition_to(SalesStage.PITCH, "Customer agreed with the summary")
            elif sig.negative:
                self.transition_to(SalesStage.DISCOVERY, "Summary rejected; rediscover")
        elif stage == SalesStage.PITCH:
            self.step = CallStep.EXPLAIN
            if sig.wants_price or self._next_step(sig) == CallStep.PRICE:
                reason = "Customer asked the price" if sig.wants_price else "Explained enough: give the price"
                sig.wants_price = True
                self.step = CallStep.PRICE
                self.transition_to(SalesStage.CLOSE_NEXT_STEP, reason)
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
            SalesStage.OPENING: "Goal: Confirm the right person, greet them, then say what the company does and ask if they want this.",
            SalesStage.DISCOVERY: "Goal: Learn their situation, current solution and pain points. One question.",
            SalesStage.NEED_IDENTIFICATION: "Goal: State the customer's main need in one short sentence and ask if correct.",
            SalesStage.PITCH: "Goal: Explain briefly what the product does for them.",
            SalesStage.OBJECTION_HANDLING: "Goal: Answer with the approved playbook for this objection. Never argue.",
            SalesStage.CLOSE_NEXT_STEP: "Goal: Move to one clear next step. Offer a live demo first; if they want to buy or negotiate, offer a meeting with the team. "
                                         "Never ask what else they want to know.",
            SalesStage.COMPLETED: "Goal: The action is confirmed. Thank them and close in one sentence.",
        }
        return guidance.get(self.current_stage, "")
