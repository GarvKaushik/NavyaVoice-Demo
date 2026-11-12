"""Tests for Hinglish Text Normalization prior to Bulbul TTS."""

from speech.normalizer import HinglishNormalizer
from speech.numbers import int_to_hindi


def test_currency_normalization():
    normalized = HinglishNormalizer.normalize_currency("Hamara Pro plan sirf ₹1,499 per month hai.")
    assert "ek hazaar chaar sau ninyaanve rupaye" in normalized
    assert "pachaas rupaye" in HinglishNormalizer.normalize_currency("Registration fee ₹50 hai.")


def test_numbers_with_thousands_commas_are_one_number():
    n = HinglishNormalizer.normalize
    assert n("Yeh plan 1,499 rupaye ka hai") == "Yeh plan ek hazaar chaar sau ninyaanve rupaye ka hai"
    assert "baarah hazaar nau sau ninyaanve" in n("Price 12,999 hai")
    assert "ek lakh unchaas hazaar nau sau ninyaanve" in n("Price 1,49,999 hai")
    assert "das lakh" in n("1,000,000 log")
    assert n("Options 1,2,3 hain") == "Options ek,do,teen hain"  # a list stays a list
    assert n("5, 10 minute") == "paanch, das minute"


def test_every_two_digit_number_has_its_own_word():
    assert int_to_hindi(34) == "chauntees"
    assert int_to_hindi(67) == "sadsath"
    assert int_to_hindi(14990) == "chaudah hazaar nau sau nabbe"
    assert int_to_hindi(1199) == "ek hazaar ek sau ninyaanve"


def test_percent_normalization():
    assert "bees percent" in HinglishNormalizer.normalize_percentages("Aapko 20% discount milega.")


def test_abbreviation_normalization():
    normalized = HinglishNormalizer.normalize_abbreviations("NavyaNexus AI CRM system DND filter ko follow karta hai.")
    assert "A I" in normalized and "C R M" in normalized and "D N D" in normalized


def test_misread_hindi_words_go_to_the_voice_in_devanagari():
    assert HinglishNormalizer.normalize("Main bhi theek hoon.") == "Main bhi ठीक hoon."
    assert HinglishNormalizer.normalize("Details WhatsApp par aa jayengi.") == "Details WhatsApp par आ जाएँगी."
    assert HinglishNormalizer.normalize("Sab ho jayenge.") == "Sab ho जाएँगे."  # no "aa" added where there was none


def test_brand_name_is_spoken_as_one_word():
    assert HinglishNormalizer.normalize("Main Navyakta AI se bol rahi hoon.") == "Main नव्यक्ता A I se bol rahi hoon."


def test_dates_times_and_ranges():
    assert "baarah November" in HinglishNormalizer.normalize("12 Nov ko demo hai")
    assert "shaam paanch baje" in HinglishNormalizer.normalize("5:00 PM par")
    out = HinglishNormalizer.normalize("4-5 ghante bachenge")
    assert "May" not in out and "chaar" in out


def test_full_pipeline():
    result = HinglishNormalizer.normalize("Nexus AI CRM Pro plan ₹1,499 mein 20% discount ke saath aata hai.")
    assert "₹" not in result and "%" not in result and not any(ch.isdigit() for ch in result)
    assert "rupaye" in result and "bees percent" in result
