"""Code-enforced style rules: no emoji, no exclamation marks.

The personality prompt asks for these, but a small model sometimes parrots an example and appends
emoji anyway (measured: 4-6 of 20 adversarial prompts on qwen3:4b-instruct). These two hard rules
are mechanical, so they are enforced here whichever engine answered. Nothing else is rewritten:
tone, length and content are left to the model.

`StyleGuard` is streaming-safe: for ANY way of splitting a text into chunks, the concatenated output
equals `apply_style(whole_text)`.
"""

from __future__ import annotations

import re

# Emoji / pictographs. ZWJ (U+200D) is deliberately NOT removed: it is legitimate in some scripts
# and invisible when orphaned. Ordinary symbols (arrows, (c), (tm), degrees, currency) are kept.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # mahjong .. symbols & pictographs ext-A, incl. flags and skin tones
    (0x2600, 0x27BF),  # misc symbols and dingbats (sun, check marks, crosses, stars)
    (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55),
    (0x231A, 0x231B), (0x23E9, 0x23F3), (0x23F8, 0x23FA),
    (0x203C, 0x203C), (0x2049, 0x2049),
    (0xFE0F, 0xFE0F),  # emoji variation selector
    (0x20E3, 0x20E3),  # keycap
)  # fmt: skip
_EMOJI = re.compile(
    "[" + "".join(f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in _EMOJI_RANGES) + "]"
)

# A run of '!' that ends a unit: followed by whitespace, a closing quote/bracket, or end of text.
# Code-like uses (!=, !important, !foo, ![image]) are not followed by those, so they survive.
_BANG_RUN = re.compile(r"!+(?=[\s\"')\]}]|\Z)")


def _rewrite(text: str) -> str:
    """`text` is complete up to a point where a following '!' run's follower is known or is EOF."""
    text = _EMOJI.sub("", text)

    def fix(m: re.Match[str]) -> str:
        before = text[m.start() - 1] if m.start() else ""
        # after whitespace / start / a full stop or question mark: just drop it; else end the clause
        return "" if before in ("", " ", "\t", "\n", ".", "?") else "."

    return _BANG_RUN.sub(fix, text)


def apply_style(text: str) -> str:
    return _rewrite(text)


class StyleGuard:
    """Incremental version of `apply_style`.

    A trailing run of '!' is held back until the next chunk shows what follows it (or the stream
    ends); everything before it is final. The last emitted character is kept so the '!' rule can see
    what precedes a held-back run.
    """

    def __init__(self) -> None:
        self._held = ""  # trailing '!' run whose follower is unknown
        self._prev = ""  # last character already emitted (look-behind for the next piece)

    def feed(self, chunk: str) -> str:
        # Strip emoji BEFORE looking for a trailing '!' run: "!" + emoji + "a" is "!a", not "!".
        s = _EMOJI.sub("", self._held + chunk)
        self._held = ""
        trailing = re.search(r"!+\Z", s)
        if trailing:
            self._held, s = trailing.group(0), s[: trailing.start()]
        return self._emit(s)

    def flush(self) -> str:
        s, self._held = self._held, ""
        return self._emit(s)

    def _emit(self, s: str) -> str:
        if not s:
            return ""
        # Emoji stripping can remove the very character `_prev` refers to only inside `s`; `_prev`
        # itself was already emitted, so it is never an emoji or a '!' and survives `_rewrite`.
        out = _rewrite(self._prev + s)[len(self._prev) :]
        if out:
            self._prev = out[-1]
        return out
