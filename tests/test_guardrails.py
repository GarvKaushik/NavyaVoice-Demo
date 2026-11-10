"""Tests for safety guardrails: FastPath, BackchannelFilter, OutputGuard and SentenceBuffer."""

from guardrails.authorization import AuthorizationContext
from guardrails.backchannel_filter import BackchannelFilter
from guardrails.fast_path_guard import FastPathGuard
from guardrails.output_guard import OutputGuard, SentenceBuffer
from speech.phrase_cache import PreRenderedPhraseCache


def test_fast_path_opt_out():
    guard = FastPathGuard()
    for phrase in ["bhai mujhe call mat karo", "please don't call me again", "mera number list se hata do",
                   "dnd par daal do", "stop calling", "Mujhe dobara call mat karna", "remove my number"]:
        res = guard.evaluate(phrase)
        assert res.triggered and res.intent == "opt_out", phrase
        assert res.response_phrase_id == PreRenderedPhraseCache.OPT_OUT_CONFIRMED


def test_fast_path_other_intents():
    guard = FastPathGuard()
    assert guard.evaluate("mujhe kisi human se baat karao").intent == "transfer_human"
    assert guard.evaluate("wrong number hai").intent == "wrong_person"
    assert guard.evaluate("abhi busy hoon, baad mein call karo").intent == "call_later"
    assert guard.evaluate("main complaint darj karunga").intent == "complaint"
    assert not guard.evaluate("haan boliye, price kya hai?").triggered
    assert not guard.evaluate("bhej do link WhatsApp pe").triggered


def test_backchannel_filter():
    f = BackchannelFilter()
    for b in ["haan", "hmm", "theek hai", "ji", "achha", "ok"]:
        assert f.is_backchannel(b) is True
    for real in ["ruko mujhe pricing samajh nahi aayi", "aap kaun bol rahe ho?", "discount kitna doge?"]:
        assert f.is_backchannel(real) is False


def test_output_guard_blocks_unauthorized_price():
    guard = OutputGuard()
    ok = guard.verify_sentence("Hamara Pro plan ₹1499 mein 20% discount ke saath aata hai.", {1499.0, 20.0}, {"discount"})
    assert ok.passed
    bad = guard.verify_sentence("Main aapko special offer mein ₹499 mein de dungi.", {1499.0, 20.0}, {"discount"})
    assert not bad.passed and bad.sanitized_sentence == guard.SAFE_FALLBACK
    assert any("499" in r for r in bad.reasons)


def test_output_guard_catches_numbers_in_words():
    guard = OutputGuard()
    auth = AuthorizationContext()
    auth.authorize_numbers([1499])
    assert not guard.verify_sentence("Aapko pachaas percent off milega.", context=auth).passed
    assert not guard.verify_sentence("Sirf pandrah sau rupaye mein.", context=auth).passed
    assert guard.verify_sentence("Bas do minute lagenge, bhej do details.", context=auth).passed


def test_output_guard_restricted_terms_and_dates():
    guard = OutputGuard()
    assert not guard.verify_sentence("Hamara system best in India hai with guaranteed returns.", {1499.0}, set()).passed
    assert not guard.verify_sentence("Yeh bilkul free hai.", set(), set()).passed
    auth = AuthorizationContext()
    assert not guard.verify_sentence("Demo Monday ko ho jayega.", context=auth).passed
    auth.authorize_text("Demo Monday 11 baje")
    assert guard.verify_sentence("Demo Monday 11 baje ho sakta hai.", context=auth).passed


def test_output_guard_commitments_need_tool_success():
    guard = OutputGuard()
    auth = AuthorizationContext()
    assert not guard.verify_sentence("Maine link aapke WhatsApp par bhej diya hai.", context=auth).passed
    assert guard.verify_sentence("Kya main link WhatsApp par bhej doon?", context=auth).passed  # offer, not promise
    auth.mark_action("send_payment_link")
    assert guard.verify_sentence("Maine link aapke WhatsApp par bhej diya hai.", context=auth).passed


def test_sentence_buffer_never_splits_amounts_or_abbreviations():
    buf = SentenceBuffer()
    out = buf.push("Price ₹1.") + buf.push("99 hai. Rs. 500 alag se. ")
    out += buf.flush()
    assert out == ["Price ₹1.99 hai.", "Rs. 500 alag se."]
