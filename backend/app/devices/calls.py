"""Phone calls on command: "call Asha" asks the paired phone to show a confirmation.

ULTRON never dials. It sends a proposal to phones that were paired with the `call` capability; the
phone shows who and which number, and dials only after the operator taps Confirm there. The contact
list stays on the phone: the backend sees only the name (or number) the operator spoke.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from app.core.services import Services
from app.devices import prefs

# The whole message must be the command, so "call me a taxi" or "let's call it a day" never match.
_COMMAND = re.compile(
    r"^\s*(?:(?:please|ultron|hey|can you|could you|would you)[\s,:]+)*"
    r"(?:call|phone|ring|dial)\s+(?P<who>.{1,60}?)\s*[.?!]*\s*$",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"^\+?[\d][\d ()\-.]{4,19}$")
_NOT_A_TARGET = {
    "me", "it", "him", "her", "them", "us", "you", "this", "that", "back", "a", "an", "the",
    "off", "up", "out", "in", "on", "now", "later", "again", "home", "someone", "somebody",
    "anyone", "everyone", "my", "your", "our",
}  # fmt: skip
_NAME = re.compile(r"^[^\W\d_][\w'’.\-]*(?: [^\W\d_][\w'’.\-]*){0,3}$", re.UNICODE)


@dataclass(frozen=True)
class CallTarget:
    kind: str  # "name" | "number"
    value: str


def parse_call(text: str) -> CallTarget | None:
    m = _COMMAND.match(text or "")
    if not m:
        return None
    who = " ".join(m.group("who").split())
    if _NUMBER.match(who):
        digits = re.sub(r"[^\d+]", "", who)
        return CallTarget("number", digits) if 5 <= len(digits) <= 16 else None
    first = who.split(" ")[0].lower()
    if first in _NOT_A_TARGET or who.lower() in _NOT_A_TARGET or not _NAME.match(who):
        return None
    return CallTarget("name", who)


async def propose_call(services: Services, text: str) -> dict | None:
    """If `text` is a call command, ask the phones to confirm it. Returns the report, else None."""
    target = parse_call(text)
    if target is None or services.hub is None:
        return None
    request_id = secrets.token_urlsafe(9)
    confirm = await prefs.get(services, "call_confirm")  # the operator's on/off switch
    sent = await services.hub.enqueue_for(
        "call",
        "call_proposal",
        {
            "request_id": request_id,
            "kind": target.kind,
            "target": target.value,
            "confirm": confirm,  # False: the phone places the call itself (after a short countdown)
        },
    )
    if not sent:
        status = "no_phone"
    else:
        status = "awaiting_confirmation" if confirm else "placing"
    return {
        "type": "call",
        "request_id": request_id,
        "target": target.value,
        "status": status,
        "devices": sent,
    }


def action_note(actions: list[dict]) -> str | None:
    """What the model must say about actions it did not and cannot perform itself."""
    notes: list[str] = []
    for a in actions:
        if a.get("type") == "call":
            if a["status"] == "placing":
                notes.append(
                    f"The operator asked to call {a['target']} and has turned call confirmation "
                    "off, so their phone is placing the call itself after a short countdown they "
                    "can cancel. Say you are calling, in one short sentence. You cannot dial "
                    "anything yourself."
                )
            elif a["status"] == "awaiting_confirmation":
                notes.append(
                    f"The operator asked to call {a['target']}. Their phone has been asked to show "
                    "a confirmation; nothing is dialled until they tap Confirm on the phone. Say "
                    "that in one short sentence. You cannot dial anything yourself."
                )
            else:
                notes.append(
                    f"The operator asked to call {a['target']}, but no phone with calling "
                    "permission is paired, so nothing happened. Say so plainly in one short "
                    "sentence."
                )
        elif a.get("type") == "reminder":
            if a["status"] == "created":
                where = (
                    "It will be delivered to their paired phone."
                    if a.get("devices")
                    else "No phone is paired yet, so it will be delivered once one is."
                )
                notes.append(
                    f'A reminder was set: "{a["text"]}" for {a["due_local"]}. {where} Confirm '
                    "it in one short sentence, using exactly that time as written (it is already "
                    "in the operator's own time zone)."
                )
            else:
                notes.append(
                    "The operator asked for a reminder but NO reminder was set: "
                    f"{a['why']} Tell them that, and what to say instead, in one short sentence."
                )
    return " ".join(notes) or None
