"""Deterministic parsing of Hinglish callback times ("kal subah", "shaam 5 baje", "2 ghante baad")."""

import re
from datetime import datetime, timedelta
from typing import Optional

import pytz

from config.settings import get_settings

_PART_OF_DAY = {"subah": 11, "dopahar": 14, "shaam": 17, "evening": 17, "morning": 11, "afternoon": 14}
_WORD_NUM = {"ek": 1, "do": 2, "teen": 3, "chaar": 4, "char": 4, "paanch": 5, "panch": 5, "chhah": 6, "che": 6,
             "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyaarah": 11, "gyarah": 11, "baarah": 12, "barah": 12}


def _num(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    return _WORD_NUM.get(token)


def parse_callback_time(text: str, now: Optional[datetime] = None, tz_name: Optional[str] = None) -> Optional[datetime]:
    """Returns the requested callback time as an aware datetime in the lead's zone, or None."""
    s = get_settings()
    tz = pytz.timezone(tz_name or s.TIMEZONE)
    now = now.astimezone(tz) if now else datetime.now(tz)
    t = text.lower()

    m = re.search(r"\b(\d+|ek|do|teen|aadhe|aadha)\s*(ghante|ghanta|hour|hours|minute|minutes|min)\s*(baad|mein|me|later)", t)
    if m:
        qty = 0.5 if m.group(1).startswith("aadh") else (_num(m.group(1)) or 1)
        unit = timedelta(hours=qty) if m.group(2).startswith(("ghant", "hour")) else timedelta(minutes=qty)
        return clamp_to_window(now + unit, tz)

    day = now
    if re.search(r"\bparso\b|day after tomorrow", t):
        day = now + timedelta(days=2)
    elif re.search(r"\bkal\b|tomorrow", t):
        day = now + timedelta(days=1)
    elif re.search(r"agle\s*hafte|next\s*week", t):
        day = now + timedelta(days=7)

    hour = None
    minute = 0
    m = re.search(r"\b(\d{1,2}|ek|do|teen|chaar|char|paanch|panch|chhah|che|saat|aath|nau|das|gyaarah|gyarah|baarah|barah)(?::(\d{2}))?\s*(baje|bje|o'?clock|am|pm)\b", t)
    if m:
        hour = _num(m.group(1))
        if hour is not None:
            minute = int(m.group(2) or 0) % 60
            suffix = m.group(3)
            evening = any(w in t for w in ("shaam", "dopahar", "raat", "evening", "afternoon")) or suffix == "pm"
            if evening and hour < 12:
                hour += 12
            elif suffix == "baje" and hour <= 7:
                hour += 12  # "5 baje" during business means 5 PM
    if hour is None:
        for word, h in _PART_OF_DAY.items():
            if word in t:
                hour = h
                break

    if hour is None and day is now:
        return None
    if hour is None:
        hour = 11
    candidate = day.replace(hour=min(hour, 23), minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return clamp_to_window(candidate, tz)


def clamp_to_window(when: datetime, tz) -> datetime:
    """Moves a time into the permitted calling window."""
    s = get_settings()
    start, end = s.COMPLIANCE_CALLING_START_HOUR, s.COMPLIANCE_CALLING_END_HOUR
    local = when.astimezone(tz)
    if local.hour < start:
        local = local.replace(hour=start, minute=0)
    elif local.hour >= end:
        local = (local + timedelta(days=1)).replace(hour=start + 1, minute=0)
    return local


def speak_time(when: datetime) -> str:
    """Hinglish phrase for a callback time relative to today, e.g. 'kal shaam 5 baje'."""
    s = get_settings()
    now = datetime.now(when.tzinfo or pytz.timezone(s.TIMEZONE))
    delta_days = (when.date() - now.date()).days
    day = {0: "aaj", 1: "kal", 2: "parso"}.get(delta_days, when.strftime("%d %B"))
    h = when.hour
    part = "subah" if h < 12 else "dopahar" if h < 16 else "shaam" if h < 20 else "raat"
    h12 = h % 12 or 12
    clock = f"{h12}:{when.minute:02d}" if when.minute else f"{h12}"
    return f"{day} {part} {clock} baje"
