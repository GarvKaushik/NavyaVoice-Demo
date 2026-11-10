"""Roman Hinglish to Devanagari, because the local Hindi voice reads Roman text with English sounds.

The agent's vocabulary is listed below, including its English words spelled the way they are said. Anything else goes
through simple spelling rules, which suit Hindi words but can mispronounce English words and names not on the list.
"""

import re
from typing import Dict, List

WORDS: Dict[str, str] = {
    # Hindi words the spelling rules would get wrong
    "hai": "है", "hain": "हैं", "ji": "जी", "mein": "में", "me": "में", "aur": "और", "kya": "क्या", "ko": "को", "aap": "आप",
    "karta": "करता", "baje": "बजे", "ka": "का", "ke": "के", "ki": "की", "ya": "या", "main": "मैं", "mai": "मैं", "liye": "लिए",
    "hoon": "हूँ", "hun": "हूँ", "kal": "कल", "se": "से", "aapko": "आपको", "aapke": "आपके", "yeh": "यह", "ye": "ये",
    "yehi": "यही", "yahi": "यही", "aapka": "आपका", "aapki": "आपकी", "aapne": "आपने", "apna": "अपना", "apne": "अपने",
    "apni": "अपनी", "ho": "हो", "subah": "सुबह", "abhi": "अभी", "samajh": "समझ", "karte": "करते", "par": "पर", "pe": "पे",
    "theek": "ठीक", "thik": "ठीक", "chahenge": "चाहेंगे", "karke": "करके", "kaise": "कैसे", "kaisa": "कैसा", "kar": "कर",
    "doon": "दूँ", "bhej": "भेज", "kaunsa": "कौनसा", "kaunse": "कौनसे", "kaun": "कौन", "bilkul": "बिल्कुल", "rahi": "रही",
    "sahi": "सही", "dopahar": "दोपहर", "koi": "कोई", "sakti": "सकती", "sakta": "सकता", "sakte": "सकते", "ek": "एक", "aa": "आ",
    "paas": "पास", "rahega": "रहेगा", "jo": "जो", "gaya": "गया", "gayi": "गई", "lagta": "लगता", "lagti": "लगती",
    "lagte": "लगते", "jayengi": "जाएँगी", "jayenge": "जाएँगे", "jayega": "जाएगा", "nahi": "नहीं", "nahin": "नहीं",
    "bhi": "भी", "khud": "खुद", "karti": "करती", "sirf": "सिर्फ़", "dekhna": "देखना", "baat": "बात", "karne": "करने",
    "karna": "करना", "raha": "रहा", "har": "हर", "chahiye": "चाहिए", "do": "दो", "baare": "बारे", "aaj": "आज", "kaam": "काम",
    "lena": "लेना", "dhanyawaad": "धन्यवाद", "dhanyavaad": "धन्यवाद", "toh": "तो", "to": "तो", "hoti": "होती", "hota": "होता",
    "hote": "होते", "bachana": "बचाना", "bachata": "बचाता", "bachta": "बचता", "bachega": "बचेगा", "bach": "बच", "bache": "बचे",
    "bacha": "बचा", "bachane": "बचाने", "seedhi": "सीधी", "seedha": "सीधा", "seedhe": "सीधे", "din": "दिन", "rahe": "रहे",
    "rahein": "रहें", "kam": "कम", "maine": "मैंने", "jagah": "जगह", "kisi": "किसी", "zyada": "ज़्यादा", "mil": "मिल",
    "iska": "इसका", "iski": "इसकी", "iske": "इसके", "shubh": "शुभ", "hoga": "होगा", "hogi": "होगी", "hongi": "होंगी",
    "hon": "हों", "kaafi": "काफ़ी", "hi": "ही", "mushkil": "मुश्किल", "bol": "बोल", "yaani": "यानी", "isi": "इसी", "usi": "उसी",
    "waqt": "वक़्त", "deta": "देता", "deti": "देती", "dete": "देते", "karenge": "करेंगे", "bahut": "बहुत", "lag": "लग",
    "shaam": "शाम", "de": "दे", "samay": "समय", "aisa": "ऐसा", "aise": "ऐसे", "samjhi": "समझी", "samjha": "समझा",
    "samjhaun": "समझाऊँ", "samajhna": "समझना", "samajhni": "समझनी", "sabse": "सबसे", "bataun": "बताऊँ", "batun": "बताऊँ",
    "isse": "इससे", "ise": "इसे", "agar": "अगर", "hum": "हम", "na": "ना", "diya": "दिया", "dene": "देने", "batati": "बताती",
    "yahin": "यहीं", "milte": "मिलते", "milti": "मिलती", "milenge": "मिलेंगे", "milengi": "मिलेंगी", "kis": "किस",
    "karega": "करेगा", "karegi": "करेगी", "taraf": "तरफ़", "shukriya": "शुक्रिया", "karwa": "करवा", "karwata": "करवाता",
    "karwaungi": "करवाऊँगी", "is": "इस", "jata": "जाता", "jaata": "जाता", "jati": "जाती", "jaati": "जाती", "jaate": "जाते",
    "pasand": "पसंद", "paayenge": "पाएँगे", "paati": "पाती", "log": "लोग", "logon": "लोगों", "laga": "लगा", "lage": "लगे",
    "lagega": "लगेगा", "lagne": "लगने", "achha": "अच्छा", "accha": "अच्छा", "acha": "अच्छा", "hamari": "हमारी", "hamare": "हमारे",
    "thodi": "थोड़ी", "thoda": "थोड़ा", "der": "देर", "haan": "हाँ", "han": "हाँ", "aage": "आगे", "jisse": "जिससे",
    "saath": "साथ", "kitni": "कितनी", "kitne": "कितने", "kitna": "कितना", "itna": "इतना", "itne": "इतने", "bana": "बना",
    "banata": "बनाता", "banana": "बनाना", "aayega": "आएगा", "rakhoon": "रखूँ", "rakhun": "रखूँ", "rakhna": "रखना",
    "rakh": "रख", "rakhte": "रखते", "rakhungi": "रखूँगी", "sudharna": "सुधारना", "rehti": "रहती", "rehta": "रहता",
    "rehte": "रहते", "kuch": "कुछ", "sunke": "सुनके", "pehle": "पहले", "ja": "जा", "kaha": "कहा", "zaroor": "ज़रूर",
    "badhiya": "बढ़िया", "kab": "कब", "dono": "दोनों", "chahte": "चाहते", "chahein": "चाहें", "isme": "इसमें", "ismein": "इसमें",
    "inmein": "इनमें", "jisme": "जिसमें", "baad": "बाद", "bata": "बता", "karni": "करनी", "soch": "सोच", "jaanna": "जानना",
    "jaana": "जाना", "jaan": "जान", "madad": "मदद", "tarah": "तरह", "namaste": "नमस्ते", "navya": "नव्या", "priya": "प्रिया",
    "hata": "हटा", "rukiye": "रुकिए", "sun": "सुन", "aawaz": "आवाज़", "dobara": "दोबारा", "kripya": "कृपया", "bane": "बने",
    "rahiye": "रहिए", "sabhi": "सभी", "unse": "उनसे", "unko": "उनको", "maaf": "माफ़", "kijiyega": "कीजिएगा", "kijiye": "कीजिए",
    "dijiye": "दीजिए", "galat": "ग़लत", "galti": "ग़लती", "naraz": "नाराज़", "khatam": "ख़त्म", "shikayat": "शिकायत",
    "darj": "दर्ज", "li": "ली", "sampark": "संपर्क", "le": "ले", "leta": "लेता", "leti": "लेती", "lein": "लें", "lenge": "लेंगे",
    "hone": "होने", "hona": "होना", "denge": "देंगे", "dein": "दें", "aasaan": "आसान", "taaki": "ताकि", "jaye": "जाए",
    "isliye": "इसलिए", "roz": "रोज़", "cheez": "चीज़", "chala": "चला", "chalta": "चलता", "chal": "चल", "dungi": "दूँगी",
    "aati": "आती", "aate": "आते", "aaya": "आया", "sab": "सब", "pata": "पता", "matlab": "मतलब", "dikkat": "दिक्कत",
    "kare": "करे", "karein": "करें", "kiya": "किया", "hisaab": "हिसाब", "dikhenge": "दिखेंगे", "zarurat": "ज़रूरत",
    "wala": "वाला", "wali": "वाली", "kabhi": "कभी", "bataiyega": "बताइएगा", "bataiye": "बताइए", "dikha": "दिखा", "dikh": "दिख",
    "poochna": "पूछना", "taur": "तौर", "karun": "करूँ", "karoon": "करूँ", "bhejti": "भेजती", "poora": "पूरा", "pura": "पूरा",
    "alawa": "अलावा", "phir": "फिर", "badle": "बदले", "bhejoon": "भेजूँ", "bhejun": "भेजूँ", "bheju": "भेजूँ",
    "bhejungi": "भेजूँगी", "jab": "जब", "tab": "तब", "badhna": "बढ़ना", "tak": "तक", "bas": "बस", "bataoge": "बताओगे",
    "batao": "बताओ", "ab": "अब", "philhaal": "फ़िलहाल", "filhaal": "फ़िलहाल", "jaldi": "जल्दी", "alag": "अलग", "daalti": "डालती",
    "mehenga": "महँगा", "badi": "बड़ी", "bada": "बड़ा", "chhut": "छूट", "kara": "करा", "jisko": "जिसको", "jaan": "जान",
    "sawaal": "सवाल", "tareeka": "तरीक़ा", "dekh": "देख", "dekhenge": "देखेंगे", "shamil": "शामिल", "yaad": "याद",
    "dilata": "दिलाता", "dikhata": "दिखाता", "sambhalta": "संभालता", "nazar": "नज़र", "baar": "बार", "samaapt": "समाप्त",
    "tha": "था", "saalana": "सालाना", "akele": "अकेले", "chhota": "छोटा", "dinon": "दिनों", "santusht": "संतुष्ट",
    "rupaye": "रुपये", "paise": "पैसे", "hazaar": "हज़ार", "sau": "सौ", "lakh": "लाख", "crore": "करोड़", "saadhe": "साढ़े",
    "barah": "बारह", "baarah": "बारह", "gyarah": "ग्यारह", "gyaarah": "ग्यारह", "bajkar": "बजकर",
    # English words, spelled the way they are said
    "nexus": "नेक्सस", "navyanexus": "नव्या नेक्सस", "rajesh": "राजेश", "pro": "प्रो", "leads": "लीड्स", "lead": "लीड",
    "call": "कॉल", "calls": "कॉल्स", "calling": "कॉलिंग", "demo": "डेमो", "details": "डिटेल्स", "detail": "डिटेल",
    "detailed": "डिटेल्ड", "update": "अपडेट", "updates": "अपडेट्स", "manual": "मैनुअल", "manually": "मैनुअली", "minute": "मिनट",
    "minutes": "मिनट", "min": "मिनट", "time": "टाइम", "price": "प्राइस", "link": "लिंक", "per": "पर", "month": "मंथ",
    "monthly": "मंथली", "follow": "फॉलो", "whatsapp": "व्हाट्सऐप", "software": "सॉफ़्टवेयर", "up": "अप", "ups": "अप्स", "sir": "सर",
    "tracking": "ट्रैकिंग", "track": "ट्रैक", "sales": "सेल्स", "excel": "एक्सेल", "live": "लाइव", "payment": "पेमेंट",
    "automatic": "ऑटोमैटिक", "automatically": "ऑटोमैटिकली", "reminders": "रिमाइंडर्स", "hinglish": "हिंगलिश",
    "pipeline": "पाइपलाइन", "book": "बुक", "offer": "ऑफ़र", "slot": "स्लॉट", "slots": "स्लॉट्स", "use": "यूज़", "tool": "टूल",
    "team": "टीम", "entry": "एंट्री", "entries": "एंट्रीज़", "analytics": "एनालिटिक्स", "need": "नीड", "special": "स्पेशल",
    "discount": "डिस्काउंट", "problem": "प्रॉब्लम", "automate": "ऑटोमेट", "automated": "ऑटोमेटेड", "automation": "ऑटोमेशन",
    "issue": "इशू", "auto": "ऑटो", "convenient": "कन्वीनियंट", "useful": "यूज़फ़ुल", "hello": "हेलो", "feature": "फ़ीचर",
    "features": "फ़ीचर्स", "plan": "प्लान", "handle": "हैंडल", "number": "नंबर", "thank": "थैंक", "thanks": "थैंक्स", "you": "यू",
    "process": "प्रोसेस", "prefer": "प्रिफ़र", "proper": "प्रॉपर", "properly": "प्रॉपरली", "available": "अवेलेबल", "miss": "मिस",
    "missed": "मिस्ड", "missing": "मिसिंग", "expert": "एक्सपर्ट", "experts": "एक्सपर्ट्स", "manage": "मैनेज", "effort": "एफ़र्ट",
    "manageable": "मैनेजेबल", "record": "रिकॉर्ड", "daily": "डेली", "suit": "सूट", "second": "सेकंड", "phone": "फ़ोन",
    "waste": "वेस्ट", "system": "सिस्टम", "close": "क्लोज़", "through": "थ्रू", "improve": "इम्प्रूव", "approx": "अप्रॉक्स",
    "approximate": "अप्रॉक्सिमेट", "priority": "प्रायोरिटी", "check": "चेक", "short": "शॉर्ट", "confirm": "कन्फ़र्म",
    "arrange": "अरेंज", "direct": "डायरेक्ट", "directly": "डायरेक्टली", "workload": "वर्कलोड", "consuming": "कंज़्यूमिंग",
    "the": "द", "integration": "इंटीग्रेशन", "dialer": "डायलर", "yearly": "ईयरली", "pain": "पेन", "visibility": "विज़िबिलिटी",
    "solve": "सॉल्व", "performance": "परफ़ॉर्मेंस", "next": "नेक्स्ट", "step": "स्टेप", "assistant": "असिस्टेंट",
    "quality": "क्वालिटी", "training": "ट्रेनिंग", "list": "लिस्ट", "network": "नेटवर्क", "senior": "सीनियर", "product": "प्रोडक्ट",
    "transfer": "ट्रांसफ़र", "line": "लाइन", "busy": "बिज़ी", "callback": "कॉलबैक", "exact": "एग्ज़ैक्ट", "support": "सपोर्ट",
    "data": "डेटा", "fast": "फ़ास्ट", "easy": "ईज़ी", "workflow": "वर्कफ़्लो", "option": "ऑप्शन", "options": "ऑप्शंस",
    "dialing": "डायलिंग", "dial": "डायल", "clear": "क्लियर", "clearly": "क्लियरली", "focus": "फ़ोकस", "choose": "चूज़",
    "amount": "अमाउंट", "interested": "इंटरेस्टेड", "diary": "डायरी", "worth": "वर्थ", "simplify": "सिंप्लीफ़ाई",
    "onboarding": "ऑनबोर्डिंग", "lock": "लॉक", "in": "इन", "reduce": "रिड्यूस", "end": "एंड", "lose": "लूज़", "action": "एक्शन",
    "already": "ऑलरेडी", "completed": "कंप्लीटेड", "complete": "कंप्लीट", "start": "स्टार्ट", "based": "बेस्ड", "delay": "डिले",
    "stage": "स्टेज", "setup": "सेटअप", "import": "इम्पोर्ट", "set": "सेट", "response": "रिस्पॉन्स", "fields": "फ़ील्ड्स",
    "name": "नेम", "outcome": "आउटकम", "outcomes": "आउटकम्स", "weak": "वीक", "perfect": "परफ़ेक्ट", "introduce": "इंट्रोड्यूस",
    "smooth": "स्मूथ", "great": "ग्रेट", "billing": "बिलिंग", "important": "इम्पॉर्टेंट", "tension": "टेंशन", "status": "स्टेटस",
    "cut": "कट", "help": "हेल्प", "explain": "एक्सप्लेन", "normal": "नॉर्मल", "mobile": "मोबाइल", "notes": "नोट्स",
    "roughly": "रफ़ली", "outbound": "आउटबाउंड", "walk": "वॉक", "point": "पॉइंट", "load": "लोड", "reporting": "रिपोर्टिंग",
    "report": "रिपोर्ट", "pick": "पिक", "size": "साइज़", "share": "शेयर", "shift": "शिफ़्ट", "cost": "कॉस्ट",
    "comparison": "कंपैरिज़न", "decision": "डिसीज़न", "respect": "रिस्पेक्ट", "try": "ट्राई", "timely": "टाइमली",
    "concern": "कंसर्न", "metric": "मेट्रिक", "provide": "प्रोवाइड", "conversation": "कन्वर्सेशन", "annual": "एनुअल",
    "starter": "स्टार्टर", "rep": "रेप", "level": "लेवल", "basic": "बेसिक", "india": "इंडिया", "day": "डे", "money": "मनी",
    "back": "बैक", "guarantee": "गारंटी", "customer": "कस्टमर", "refund": "रिफ़ंड", "dashboard": "डैशबोर्ड", "click": "क्लिक",
    "subscription": "सब्सक्रिप्शन", "cancel": "कैंसल", "period": "पीरियड", "recordings": "रिकॉर्डिंग्स", "recording": "रिकॉर्डिंग",
    "mumbai": "मुंबई", "center": "सेंटर", "encrypted": "एन्क्रिप्टेड", "act": "एक्ट", "ok": "ओके", "okay": "ओके", "yes": "यस",
    "no": "नो", "ma'am": "मैम", "madam": "मैडम", "percent": "परसेंट",
}

LETTERS: Dict[str, str] = {
    "A": "ए", "B": "बी", "C": "सी", "D": "डी", "E": "ई", "F": "एफ़", "G": "जी", "H": "एच", "I": "आई", "J": "जे", "K": "के", "L": "एल",
    "M": "एम", "N": "एन", "O": "ओ", "P": "पी", "Q": "क्यू", "R": "आर", "S": "एस", "T": "टी", "U": "यू", "V": "वी", "W": "डब्ल्यू",
    "X": "एक्स", "Y": "वाई", "Z": "ज़ेड",
}

_CONSONANTS: Dict[str, str] = {
    "chh": "छ", "kh": "ख", "gh": "घ", "ch": "च", "jh": "झ", "th": "थ", "dh": "ध", "ph": "फ", "bh": "भ", "sh": "श",
    "k": "क", "g": "ग", "j": "ज", "t": "त", "d": "द", "n": "न", "p": "प", "b": "ब", "m": "म", "y": "य", "r": "र", "l": "ल",
    "v": "व", "w": "व", "s": "स", "h": "ह", "z": "ज़", "f": "फ़", "q": "क", "c": "क", "x": "क्स",
}
# Long spellings first. Each vowel is (sign after a consonant, letter on its own).
_VOWELS: Dict[str, tuple] = {
    "aa": ("ा", "आ"), "ee": ("ी", "ई"), "oo": ("ू", "ऊ"), "ai": ("ै", "ऐ"), "au": ("ौ", "औ"), "ei": ("े", "ए"),
    "a": ("", "अ"), "i": ("ि", "इ"), "u": ("ु", "उ"), "e": ("े", "ए"), "o": ("ो", "ओ"),
}
_FINAL_LONG = {"a": ("ा", "आ"), "i": ("ी", "ई"), "u": ("ू", "ऊ")}  # in Roman Hindi, a final vowel is the long one
_VIRAMA = "्"
_TOKEN = re.compile(r"chh|kh|gh|ch|jh|th|dh|ph|bh|sh|aa|ee|oo|ai|au|ei|[a-z]")
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def _by_rules(word: str) -> str:
    tokens: List[str] = _TOKEN.findall(word)
    out = ""
    for i, token in enumerate(tokens):
        if token in _CONSONANTS:
            out += _CONSONANTS[token]
            if i + 1 < len(tokens) and tokens[i + 1] in _CONSONANTS:
                out += _VIRAMA
            continue
        vowel = _FINAL_LONG[token] if i == len(tokens) - 1 and token in _FINAL_LONG else _VOWELS[token]
        out += vowel[0] if i and tokens[i - 1] in _CONSONANTS else vowel[1]
    return out


def _word(match: re.Match) -> str:
    word = match.group(0)
    known = WORDS.get(word.lower())
    if word.isupper() and (len(word) == 1 or not known):
        return " ".join(LETTERS[c] for c in word)
    return known or _by_rules(word.lower().replace("'", ""))


def to_devanagari(text: str) -> str:
    """Devanagari already in the text passes through unchanged."""
    text = _WORD.sub(_word, text or "")
    return re.sub(r"\s+", " ", text.replace("—", ", ").replace("–", ", ").replace(";", ",")).strip()
