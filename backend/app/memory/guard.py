"""Keep credentials and similar secrets out of long-term memory.

Best effort, not a guarantee: it catches the common shapes (API keys, bearer tokens, "my password
is ...", private-key blocks, JWTs, card numbers). Memory text is later shown to models, so a stored
secret would be a leak. The operator can still see and delete everything that is stored.
"""

from __future__ import annotations

import re

from app.core.logging import redact

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWT
    re.compile(
        r"(?i)\b(pass(word|code|phrase)|pin|api[ _-]?key|secret|token|credentials?)\b"
        r"[^.\n]{0,20}?\b(is|are|was|=|:)\s*['\"]?[^\s'\".,;]{4,}"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),  # GitHub tokens
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),  # Groq keys
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),  # long hex blobs (hashes/keys)
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def contains_secret(text: str) -> bool:
    if redact(text) != text:  # the logging patterns plus every configured secret
        return True
    if any(p.search(text) for p in _PATTERNS):
        return True
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False
