"""Reminders by chat or voice: "remind me to call the dentist in 30 minutes", "remind me at 5:30 pm
to take the bins out", "remind me tomorrow at 9am about the invoice".

Plain parsing, no model: the time is never guessed. If the message names no clear time, nothing is
created and the operator is told what to say instead. Times are read in the server's local zone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.services import Services
from app.devices import prefs, reminders

_PREFIX = re.compile(
    r"^\s*(?:(?:please|ultron|hey|can you|could you|would you)[\s,:]+)*"
    r"(?:remind me|set (?:a |an )?(?:reminder|alarm)|add (?:a )?reminder)\b"
    r"[\s,:]*(?P<rest>.*?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "sixty": 60,
}  # fmt: skip
_NUM = r"(?P<n>\d{1,4}|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_UNIT = r"(?P<u>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"
_RELATIVE = re.compile(
    rf"\bin\s+(?:(?P<half>half an hour)|{_NUM}\s*{_UNIT}|(?P<hour>an hour))\b", re.I
)
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY = r"(?P<day>today|tonight|tomorrow|" + "|".join(_DAYS) + r")"
_CLOCK = (
    r"(?:(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)?|(?P<word>noon|midnight))"
)
_DAY_WORD = re.compile(rf"\b{_DAY}\b", re.I)
_AT_CLOCK = re.compile(rf"\bat\s+{_CLOCK}\b", re.I)
_BARE_CLOCK = re.compile(
    r"\b(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))\s*(?P<ap>a\.?m\.?|p\.?m\.?)?\b"
    r"|\b(?P<h2>\d{1,2})\s*(?P<ap2>a\.?m\.?|p\.?m\.?)(?!\w)|\b(?P<word>noon|midnight)\b",
    re.I,
)


@dataclass(frozen=True)
class Parsed:
    text: str
    due: datetime


@dataclass(frozen=True)
class Unclear:
    why: str


def _clock(m: re.Match) -> tuple[int, int, bool] | None:
    """(hour, minute, ambiguous) from a clock match, or None if it is invalid. "Ambiguous" means a
    bare "5" that could be morning or evening."""
    if m.group("word"):
        return (12, 0, False) if m.group("word").lower() == "noon" else (0, 0, False)
    groups = m.groupdict()
    if groups.get("h") is None and groups.get("h2") is None:
        return None
    hour = int(groups.get("h") or groups.get("h2") or 0)
    minute = int(groups.get("m") or 0)
    ap = (groups.get("ap") or groups.get("ap2") or "").lower().replace(".", "")
    if minute > 59 or hour > 23:
        return None
    if ap:
        if not 1 <= hour <= 12:
            return None
        return hour % 12 + (12 if ap == "pm" else 0), minute, False
    return hour, minute, hour <= 12 and hour != 0


def parse_time(
    text: str, now: datetime, guess: bool = False
) -> tuple[datetime, list[tuple[int, int]]] | Unclear | None:
    """The time in `text`: (due, spans of the words that said it), Unclear, or None."""
    m = _RELATIVE.search(text)
    if m:
        if m.group("half"):
            delta = timedelta(minutes=30)
        elif m.group("hour"):
            delta = timedelta(hours=1)
        else:
            n = int(m.group("n")) if m.group("n").isdigit() else _WORDS[m.group("n").lower()]
            unit = m.group("u").lower()
            step = (
                timedelta(seconds=n) if unit.startswith("sec")
                else timedelta(minutes=n) if unit.startswith("min")
                else timedelta(hours=n) if unit.startswith(("hour", "hr"))
                else timedelta(days=n) if unit.startswith("day")
                else timedelta(weeks=n)
            )  # fmt: skip
            delta = step
        if delta < timedelta(seconds=10):
            return Unclear("That is too soon to be a reminder.")
        return now + delta, [m.span()]
    day_m = _DAY_WORD.search(text)
    clock_m = _AT_CLOCK.search(text) or _BARE_CLOCK.search(text)
    if day_m is None and clock_m is None:
        return None
    spans = [m.span() for m in (day_m, clock_m) if m]
    day = day_m.group("day").lower() if day_m else ""
    if clock_m is None:
        if guess and day == "tonight":
            clock = (20, 0, False)
        elif guess and (day == "tomorrow" or day in _DAYS):
            clock = (9, 0, False)  # a day with no time: the morning
        else:
            return Unclear(
                "I need a clock time with that, for example “tomorrow at 9 am”."
                if not guess
                else "I need a time of day with “today”, for example “today at 6 pm”."
            )
    else:
        clock = _clock(clock_m)
    if clock is None:
        return Unclear("I need a valid time, for example “at 5 pm” or “at 17:30”.")
    hour, minute, ambiguous = clock
    if ambiguous and not guess:
        return Unclear(
            "I need a clear time, for example “at 5 pm” or “at 17:30” (without am or pm I would "
            "have to guess)."
        )
    if ambiguous:  # guess: with a day named, 1-6 reads as the afternoon and 7-11 as the morning
        if day:
            hour = hour + 12 if 1 <= hour <= 6 else hour
        else:  # no day: whichever of morning or evening comes next
            candidates = []
            for h in sorted({hour % 12, hour % 12 + 12}):
                c = now.replace(hour=h, minute=minute, second=0, microsecond=0)
                candidates.append(c if c > now else c + timedelta(days=1))
            return min(candidates), spans
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day == "tomorrow":
        due += timedelta(days=1)
    elif day in _DAYS:
        due += timedelta(days=(_DAYS.index(day) - now.weekday()) % 7)
        if due <= now:
            due += timedelta(days=7)
    elif day in ("today", "tonight"):
        if due <= now:
            return Unclear("That time has already passed today.")
    elif due <= now:  # a bare clock time: the next time it occurs
        due += timedelta(days=1)
    return due, spans


_LEAD = re.compile(r"^(?:to|that|about|for|of)\s+", re.I)
_TAIL = re.compile(r"\s*(?:,|:|-)?\s*$")


def parse_reminder(text: str, now: datetime, guess: bool = False) -> Parsed | Unclear | None:
    m = _PREFIX.match(text or "")
    if not m or not m.group("rest"):
        return None
    rest = m.group("rest")
    found = parse_time(rest, now, guess)
    if found is None:
        return Unclear("I did not catch when. Say, for example, “in 20 minutes” or “at 5 pm”.")
    if isinstance(found, Unclear):
        return found
    due, spans = found
    what = rest
    for start, end in sorted(spans, reverse=True):
        what = f"{what[:start]} {what[end:]}"
    what = " ".join(what.split()).strip(" ,:-")
    what = re.sub(
        r"\s*\b(?:on|at|by|for|in)\s*$", "", what, flags=re.I
    )  # a stray "on" before a day
    what = re.sub(r"^(?:on|at|by|in)\s+", "", what, flags=re.I)
    for _ in range(3):  # "remind me at 5 pm to X" leaves "to X"
        what = _LEAD.sub("", what).strip(" ,:-.")
    if not what:
        return Unclear("What should I remind you about?")
    return Parsed(what[:300], due)


def local_words(due: datetime, now: datetime) -> str:
    """The time as the operator should hear it: their own zone, the day, and how far away it is."""
    due, now = due.astimezone(), now.astimezone()
    mins = round((due - now).total_seconds() / 60)
    away = (
        f"in {mins} minute{'s' if mins != 1 else ''}" if mins < 90
        else f"in {round(mins / 60)} hours" if mins < 36 * 60
        else f"in {round(mins / 1440)} days"
    )  # fmt: skip
    day = (
        "today"
        if due.date() == now.date()
        else "tomorrow"
        if (due.date() - now.date()).days == 1
        else due.strftime("on %A %d %B")
    )
    return f"{due.strftime('%H:%M')} {day} ({away})"


async def propose_reminder(
    services: Services, text: str, now: datetime | None = None
) -> dict | None:
    """If `text` is a reminder command, create it. Returns the report for the reply, else None."""
    now = now or datetime.now().astimezone()
    guess = await prefs.get(services, "reminder_guess")  # the operator's on/off switch
    parsed = parse_reminder(text, now, guess)
    if parsed is None:
        return None
    if isinstance(parsed, Unclear):
        return {"type": "reminder", "status": "unclear", "why": parsed.why}
    try:
        r = await reminders.create(services, parsed.text, parsed.due)
    except reminders.InvalidReminder as exc:
        return {"type": "reminder", "status": "unclear", "why": exc.message}
    return {
        "type": "reminder",
        "status": "created",
        "reminder_id": r.id,
        "text": r.text,
        "due_at": r.due_at.isoformat(),
        "due_local": local_words(r.due_at, now),
        "devices": len(await services.devices.with_capability("notify")),
    }
