"""Regression tests for problems found in a real console call: tool rounds, booking results, slots, times, stages,
unprompted prices, reply length and pronunciation."""

from datetime import datetime

import pytz

from agent.callback_time import parse_callback_time, speak_time
from agent.dialogue import MAX_TOOL_ROUNDS, DialogueEngine
from agent.knowledge import seed_knowledge
from agent.stages import CALL_FLOW, CallStep, SalesStage
from agent.tools import CallIdentity, CallTools
from guardrails.authorization import AuthorizationContext
from llm.client import LLMEvent, LLMUnavailableError, ToolCall, Usage
from speech.normalizer import HinglishNormalizer
from speech.phrase_cache import SentenceAudioCache


class RecordingLLM:
    """Replays scripted rounds and records what each request sent. An exception in a round is raised."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.requests = []

    async def stream_chat(self, messages, tools=None, extra_body=None, **kw):
        self.requests.append({"messages": list(messages), "tools": tools, "extra_body": extra_body})
        for ev in (self.rounds.pop(0) if self.rounds else [LLMEvent("text", text="Ji. ")]):
            if isinstance(ev, Exception):
                raise ev
            yield ev
        yield LLMEvent("done", usage=Usage())


def text(t):
    return LLMEvent("text", text=t)


def tool(name, args="{}", call_id="t1"):
    return LLMEvent("tool_calls", tool_calls=[ToolCall(call_id, name, args)])


def engine_with(rounds, stage=SalesStage.DISCOVERY):
    e = DialogueEngine(CallIdentity("call_fix", "LEAD-TEST-001", "CAMP-REENGAGE-Q4", "+919111111111", "Rajesh"),
                       seed_knowledge(), RecordingLLM(rounds))
    e.opening()
    e.stage.current_stage = stage
    return e


async def reply(e, said):
    scripted = await e.pre_turn(said)
    return scripted.utterances if scripted else [u async for u in e.stream_reply()]


async def test_tool_list_is_sent_on_the_last_round_with_tools_turned_off():
    rounds = [[tool("list_demo_slots")] for _ in range(MAX_TOOL_ROUNDS)] + [[text("Aapko kaunsa time theek rahega?")]]
    e = engine_with(rounds)
    out = await reply(e, "theek hai")
    last = e.llm.requests[-1]
    assert last["tools"] and last["extra_body"] == {"tool_choice": "none"}
    assert out[-1].text == "Aapko kaunsa time theek rahega?"
    assert e.llm.requests[0]["extra_body"] is None


async def test_booked_demo_is_confirmed_not_reported_as_a_network_issue():
    e = engine_with([[tool("list_demo_slots")], [text("Aapko kaunsa time theek rahega?")]])
    await reply(e, "demo dikhao")
    slot_id = next(iter(e.tools.offered_slot_phrases))
    e.llm.rounds = [[tool("book_demo", f'{{"slot_id": "{slot_id}"}}')], [LLMUnavailableError("down")]]
    out = await reply(e, "pehla wala theek hai")
    spoken = " ".join(u.text for u in out)
    assert "book ho gaya" in spoken and "Network" not in spoken
    assert e.outcome == "demo_booked" and e.pending_action is None


async def test_network_message_still_plays_when_nothing_was_completed():
    e = engine_with([[LLMUnavailableError("down")]])
    out = await reply(e, "theek hai")
    assert out[0].phrase_id == "network_issue_fallback" and e.outcome == "callback_scheduled"


async def test_callback_after_a_booking_keeps_the_booking_as_the_result():
    tools = CallTools(CallIdentity("c", "LEAD-TEST-001", "CAMP-REENGAGE-Q4", "+919000000009", "R"), seed_knowledge(),
                      AuthorizationContext())
    slots = await tools.execute("list_demo_slots", {})
    await tools.execute("book_demo", {"slot_id": slots["slots"][0]["slot_id"]})
    await tools.execute("schedule_callback", {"when": "kal subah"})
    assert tools.outcome == "demo_booked"


async def test_offered_slots_reach_the_next_turn():
    e = engine_with([[tool("list_demo_slots")], [text("Kaunsa time theek rahega?")], [text("Theek hai.")]])
    await reply(e, "demo dikhao")
    slot_id, spoken = next(iter(e.tools.offered_slot_phrases.items()))
    await reply(e, "woh wala theek hai")
    system = e.llm.requests[-1]["messages"][0]["content"]
    assert f'"{slot_id}" = {spoken}' in system


async def test_booking_accepts_the_ways_a_model_names_a_slot():
    for form in ("{id}", "slot_id {id}", "slot_{id}", "{spoken}"):
        tools = CallTools(CallIdentity("c", "LEAD-TEST-001", "CAMP-REENGAGE-Q4", "+919000000010", "R"), seed_knowledge(),
                          AuthorizationContext())
        first = (await tools.execute("list_demo_slots", {}))["slots"][0]
        chosen = form.format(id=first["slot_id"], spoken=first["spoken"])
        assert (await tools.execute("book_demo", {"slot_id": chosen}))["ok"] is True, form
    assert (await tools.execute("book_demo", {"slot_id": "slot_id 999"}))["ok"] is False


async def test_yes_to_a_price_offer_allows_the_price():
    e = engine_with([[text("Kya aap iska price jaanna chahenge?")],
                     [tool("get_price", '{"sku": "CRM-PRO"}')], [text("Nexus CRM Pro ka price ₹1,499 per month hai.")]])
    await reply(e, "theek hai")
    out = await reply(e, "ji")
    assert e.tools.tool_log[-1]["result"]["ok"] is True and "₹1,499" in out[0].text


def test_clock_times_are_parsed_correctly():
    tz = pytz.timezone("Asia/Kolkata")
    now = tz.localize(datetime(2026, 11, 5, 11, 0))
    at = lambda words: parse_callback_time(words, now=now, tz_name="Asia/Kolkata")  # noqa: E731
    assert (at("5:00 baje kar lete hain").hour, at("5:00 baje kar lete hain").minute) == (17, 0)
    assert (at("shaam 5:30 baje").hour, at("shaam 5:30 baje").minute) == (17, 30)
    assert (at("5 baje").hour, at("5 baje").minute) == (17, 0)
    assert (at("kal subah 11:30 baje").hour, at("kal subah 11:30 baje").minute) == (11, 30)
    assert at("kal subah 11:30 baje").day == 6


def test_half_hour_times_are_spoken_correctly():
    tz = pytz.timezone("Asia/Kolkata")
    when = tz.localize(datetime(2030, 1, 1, 17, 30))
    assert "shaam 5:30 baje" in speak_time(when)
    spoken = HinglishNormalizer.normalize("kal shaam 5:30 baje")
    assert spoken == "kal shaam saadhe paanch baje"
    assert "subah" not in HinglishNormalizer.normalize("kal shaam 5:00 baje")


async def test_yes_to_a_need_question_moves_the_call_forward():
    e = engine_with([[text("Follow-up miss hone ka darr rehta hai kya?")], [text("Sahi samjha na?")]])
    e.stage.confirmed_needs = ["no tracking of calls and pipeline"]
    await reply(e, "excel")
    assert e.stage.current_stage == SalesStage.DISCOVERY
    await reply(e, "ji haan")
    assert "leads get missed or not followed up" in e.stage.confirmed_needs
    assert e.stage.current_stage == SalesStage.NEED_IDENTIFICATION


async def test_yes_to_an_open_question_is_not_a_need():
    e = engine_with([[text("Manual calling mein kya dikkat aati hai, aur kaise karte hain?")], [text("Achha.")]])
    await reply(e, "excel")
    await reply(e, "ji haan")
    assert "manual calling takes too much time" not in e.stage.confirmed_needs


async def test_yes_to_a_demo_offer_goes_to_the_next_step():
    e = engine_with([[text("Kya aap live demo dekhna chahenge?")], [text("Theek hai.")]])
    await reply(e, "theek hai")
    await reply(e, "ji")
    assert e.stage.current_stage == SalesStage.CLOSE_NEXT_STEP


async def test_observer_needs_count_towards_moving_on():
    e = engine_with([[text("Achha.")]])
    e.state.identified_needs = ["follow-ups get missed", "calling takes too long"]
    await reply(e, "theek")
    assert e.stage.current_stage == SalesStage.NEED_IDENTIFICATION


async def test_short_answers_cannot_keep_the_call_in_discovery():
    e = engine_with([])
    for _ in range(4):
        await reply(e, "accha")
    assert e.stage.current_stage == SalesStage.PITCH


async def test_price_tool_refuses_until_the_customer_asks():
    e = engine_with([[tool("get_price", '{"sku": "CRM-PRO"}')],
                     [text("Nexus CRM Pro ka price ₹1,499 per month hai.")]])
    out = await reply(e, "ji haan")
    assert e.tools.tool_log[-1]["result"]["ok"] is False
    assert "1,499" not in " ".join(u.text for u in out)  # the guard stops the number without the tool
    e.llm.rounds = [[tool("get_price", '{"sku": "CRM-PRO"}')], [text("Nexus CRM Pro ka price ₹1,499 per month hai.")]]
    out = await reply(e, "iska price kya hai?")
    assert e.tools.tool_log[-1]["result"]["ok"] is True
    assert "₹1,499" in out[0].text


async def test_long_reply_is_trimmed_but_keeps_its_closing_question():
    first = "Yeh ek software hai jo aapke leads ko khud call karta hai aur har call ki details CRM mein update karta hai aur reminder bhi deta hai."
    middle = "Ismein automatic Hinglish calling, seedhi CRM entry aur analytics milte hain."
    question = "Kya aap demo dekhna chahenge?"
    e = engine_with([[text(f"{first} {middle} {question}")]])
    out = await reply(e, "theek hai")
    assert [u.text for u in out] == [first, question]


def test_nexus_is_spoken_in_devanagari():
    spoken = HinglishNormalizer.normalize("Nexus CRM Pro ek AI calling software hai.")
    assert spoken == "नेक्सस C R M Pro ek A I calling software hai."
    assert HinglishNormalizer.normalize("NavyaNexus se") == "Navya नेक्सस se"


def test_sentence_cache_follows_the_spoken_text():
    cache = SentenceAudioCache(voice="v")
    expected = SentenceAudioCache(voice="v")._key("नेक्सस C R M Pro")
    assert cache._key("Nexus CRM Pro") == expected


async def late_intents(e):
    seen = []

    async def handler(intent):
        seen.append(intent)

    e.late_intent_handler = handler
    await e._on_late_intent("opt_out", 0.95)
    return seen


async def test_short_no_is_never_an_opt_out():
    e = engine_with([])
    await e.pre_turn("ji nahi")
    assert await late_intents(e) == []
    assert (await e.tools.tool_record_opt_out())["ok"] is False


async def test_clear_complaint_about_calls_still_opts_out():
    e = engine_with([])
    await e.pre_turn("aap log mujhe bahut pareshan karte ho")
    assert await late_intents(e) == ["opt_out"]


async def test_unclear_refusal_asks_before_opting_out():
    e = engine_with([])
    await e.pre_turn("mujhe aap logon se kuch nahi lena hai")
    assert await late_intents(e) == ["opt_out_confirm"]
    asked = await e.handle_late_intent("opt_out_confirm")
    assert "hata doon" in asked.utterances[0].text
    await e.pre_turn("ji haan")
    assert e.outcome == "opted_out"


async def test_no_to_the_opt_out_question_continues_the_call():
    e = engine_with([])
    await e.pre_turn("mujhe aap logon se kuch nahi lena hai")
    await e.handle_late_intent("opt_out_confirm")
    await e.pre_turn("ji nahi")
    assert e.outcome is None and e.pending_action is None


async def link_after(question, said):
    e = engine_with([])
    e.tools.amount_heard = True
    e.last_reply = question
    await e.pre_turn(said)
    return await e.tools.tool_send_payment_link("CRM-PRO")


async def test_yes_to_another_question_does_not_send_a_link():
    assert (await link_after("Kya aap detail mein lein?", "ji haan"))["ok"] is False


async def test_yes_to_demo_or_link_does_not_send_a_link():
    assert (await link_after("Demo lenge ya payment link bhejoon?", "ji haan"))["ok"] is False


async def test_no_to_the_link_question_does_not_send_a_link():
    assert (await link_after("Kya main payment link bhej doon?", "ji nahi"))["ok"] is False


async def test_yes_to_the_link_question_sends_the_link():
    assert (await link_after("Kya main payment link bhej doon?", "ji haan"))["ok"] is True


async def test_link_needs_the_amount_to_be_heard_first():
    e = engine_with([])
    e.last_reply = "Kya main payment link bhej doon?"
    await e.pre_turn("ji haan")
    assert (await e.tools.tool_send_payment_link("CRM-PRO"))["ok"] is False


async def test_a_plan_link_goes_out_once_per_call():
    e = engine_with([])
    e.tools.amount_heard = True
    e.last_reply = "Kya main payment link bhej doon?"
    await e.pre_turn("ji haan")
    await e.tools.tool_send_payment_link("CRM-PRO")
    assert (await e.tools.tool_send_payment_link("CRM-PRO"))["already_sent"] is True


async def test_price_question_is_answered_by_code_first():
    e = engine_with([[text("Nexus CRM Pro ka price ₹1,499 per month hai. "), text("Kya aap demo dekhna chahenge?")]])
    out = await reply(e, "yeh kitne ka hai")
    assert "₹1,499" in out[0].text
    assert " ".join(u.text for u in out).count("₹1,499") == 1
    assert out[-1].text.endswith("?")


async def test_counting_question_is_not_a_price_question():
    e = engine_with([[text("Ji.")]])
    await reply(e, "kitni calls ki baat hai")
    assert e.price_requested is False and e.tools.tool_log == []


async def offered_slots(e):
    await e.tools.tool_list_demo_slots()
    first, second = list(e.tools.offered_slot_phrases.values())[:2]
    e.last_reply = f"{first} baje ya {second} baje, kaunsa theek rahega?"
    return first


async def test_choosing_an_offered_slot_books_it_in_code():
    e = engine_with([])
    await offered_slots(e)
    hour = list(e.tools._offered_slots.values())[0].hour % 12 or 12
    turn = await e.pre_turn(f"{hour} baje wala theek hai")
    assert e.outcome == "demo_booked"
    assert "book ho gaya" in turn.utterances[0].text


async def test_refusing_the_slots_books_nothing():
    e = engine_with([])
    await offered_slots(e)
    assert await e.pre_turn("ji nahi") is None
    assert e.outcome is None


def test_booking_cannot_be_claimed_without_the_tool():
    from guardrails.output_guard import OutputGuard

    assert not OutputGuard().verify_sentence("Main aapka demo book kar deti hoon.").passed
    assert OutputGuard().verify_sentence("Kya main aapka demo book kar doon?").passed


def test_name_is_removed_when_used_recently():
    e = engine_with([])
    assert e._trim_name("Rajesh ji, abhi aap kaise call karte hain?") == "Abhi aap kaise call karte hain?"
    assert e._trim_name("Samajh sakti hoon Rajesh ji, demo useful rahega.") == "Samajh sakti hoon, demo useful rahega."
    assert e._trim_name("Bilkul, Rajesh ji.") == "Bilkul."


def test_name_stays_when_the_sentence_needs_it():
    e = engine_with([])
    assert e._trim_name("Kya main Rajesh ji se baat kar rahi hoon?") == "Kya main Rajesh ji se baat kar rahi hoon?"
    assert e._trim_name("Rajesh ji ko details bhejti hoon.") == "Rajesh ji ko details bhejti hoon."
    assert e._trim_name("Aapka Rajesh ji demo.") == "Aapka Rajesh ji demo."


def test_name_returns_after_a_few_replies():
    e = engine_with([])
    e._replies_since_name = 3
    assert e._trim_name("Rajesh ji, theek hai.") == "Rajesh ji, theek hai."
    assert e._trim_name("Rajesh ji, ek baat.") == "Ek baat."


def test_new_transliteration_words():
    from speech.transliterate import to_roman

    assert to_roman("हम्म") == "hmm"
    assert to_roman("कंज्यूमिंग") == "consuming"


async def test_confirming_who_they_are_gets_a_greeting_not_a_pitch():
    e = engine_with([], stage=SalesStage.OPENING)
    turn = await e.pre_turn("haan Rajesh speaking")
    assert turn.utterances[0].text == "Hello Rajesh ji, aap kaise hain?"


async def test_two_noes_to_the_offer_end_the_call_politely():
    e = engine_with([], stage=SalesStage.OPENING)
    e.stage.step = CallStep.OFFER
    assert await e.pre_turn("ji nahi") is None
    turn = await e.pre_turn("nahi")
    assert e.outcome == "not_interested" and turn.intent == "permission_denied"


async def test_first_no_to_the_offer_cannot_hang_up():
    e = engine_with([], stage=SalesStage.OPENING)
    e.stage.step = CallStep.OFFER
    await e.pre_turn("ji nahi")
    assert (await e.tools.tool_end_call("not_interested"))["ok"] is False
    assert e.pending_action is None


async def test_yes_to_a_demo_after_explaining_also_gets_the_price():
    e = engine_with([], stage=SalesStage.PITCH)
    e.stage.explanations = 2
    e.last_reply = "Kya aap demo dekhna chahenge?"
    await e.pre_turn("theek hai")
    assert e.price_requested and e.stage.current_stage == SalesStage.CLOSE_NEXT_STEP


def test_every_step_has_a_plan():
    assert set(CALL_FLOW) == set(CallStep)


async def test_goodbye_after_a_booking_is_fixed_and_ends_the_call():
    e = engine_with([])
    e.tools.outcome = "demo_booked"
    e.stage.transition_to(SalesStage.COMPLETED, "test")
    turn = await e.pre_turn("dhanyavaad")
    assert turn.intent == "goodbye" and e.pending_action.kind == "end_call"
    assert e.outcome == "demo_booked"


async def test_a_question_after_a_booking_is_still_answered():
    e = engine_with([])
    e.tools.outcome = "demo_booked"
    e.stage.transition_to(SalesStage.COMPLETED, "test")
    assert await e.pre_turn("demo kaise hoga") is None
