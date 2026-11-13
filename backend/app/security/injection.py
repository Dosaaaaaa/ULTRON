"""Detect text that tries to give the model orders. Applied to third-party text before it is shown
to a model (web results; later phone notifications and fetched pages).

This lowers the risk, it does not remove it: a determined writer can phrase an attack no pattern
list anticipates. The real protections are that such text is framed as DATA, that the model has no
tools yet, and that anything with side effects (calls, files) needs the operator's own approval.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import IntEnum


class Severity(IntEnum):
    NONE = 0
    MEDIUM = 1  # suspicious wording: keep the source, drop the text
    HIGH = 2  # an instruction aimed at the model: drop the whole item


@dataclass(frozen=True)
class Finding:
    name: str
    severity: Severity


_ZERO_WIDTH = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]")
_SPACES = re.compile(r"[ \t\f\r\v\u00a0]+")

_I = re.IGNORECASE
_RULES: list[tuple[str, Severity, re.Pattern[str]]] = [
    (
        "override_instructions",
        Severity.HIGH,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,30}\b(?:previous|prior|above|"
            r"earlier|preceding|all|your|these|the)\b.{0,20}\b(?:instructions?|prompts?|rules?|"
            r"guidelines?|directions?|programming|constraints?)\b",
            _I,
        ),
    ),
    (
        "new_identity",
        Severity.HIGH,
        re.compile(
            r"\b(?:you are now|from now on,? you|you must now|pretend (?:to be|you are)|"
            r"act as if you (?:are|were)|your new (?:role|instructions?|task))\b",
            _I,
        ),
    ),
    (
        "reveal_prompt",
        Severity.HIGH,
        re.compile(
            r"\b(?:reveal|print|show|repeat|output|leak|tell me)\b.{0,30}\b(?:system|hidden|"
            r"initial|original)\b.{0,10}\b(?:prompt|instructions?|message)\b",
            _I,
        ),
    ),
    (
        "chat_template_tokens",
        Severity.HIGH,
        re.compile(
            r"<\|?(?:im_start|im_end|system|assistant|endoftext)\|?>|\[/?INST\]|<<SYS>>|"
            r"^\s*#{1,3}\s*(?:system|assistant|instruction)s?\b",
            _I | re.MULTILINE,
        ),
    ),
    (
        "role_marker",  # "System: ..." also opens ordinary headlines, so this only drops the text
        Severity.MEDIUM,
        re.compile(r"^\s*(?:system|assistant|developer)\s*:", _I | re.MULTILINE),
    ),
    (
        "address_the_ai",
        Severity.HIGH,
        re.compile(
            r"\b(?:attention|note to|message for|instructions? for|important to)\b.{0,15}\b"
            r"(?:ai|assistant|llm|language model|chatbot|ultron|model)\b",
            _I,
        ),
    ),
    (
        "conceal_from_user",
        Severity.HIGH,
        re.compile(
            r"\b(?:do not|don't|never|without)\b.{0,15}\b(?:tell|inform|mention|alert|notify|"
            r"show)\b.{0,20}\b(?:the )?(?:user|operator|human)\b",
            _I,
        ),
    ),
    (
        "act_on_the_operators_data",
        Severity.HIGH,
        re.compile(
            r"\b(?:call|dial|phone|text|sms|email|forward|upload|send|delete|erase|wipe|execute|"
            r"install)\b.{0,40}\b(?:the user'?s|all (?:the )?(?:files|contacts|messages|"
            r"emails|photos)|passwords?|credentials?|api keys?|tokens?|private keys?)\b",
            _I,
        ),
    ),
    (
        "act_on_this_number",
        Severity.MEDIUM,
        re.compile(r"\b(?:call|dial|phone|text|sms)\b.{0,20}\b(?:this|that) (?:number|link)\b", _I),
    ),
    (
        "exfiltrate_via_link",
        Severity.HIGH,
        re.compile(r"!\[[^\]]*\]\(\s*https?://[^)\s]*[?&][^)\s]*=", _I),  # markdown image + query
    ),
    (
        "encoded_payload",
        Severity.MEDIUM,
        re.compile(r"\b(?:base64|rot13|hex[- ]?encoded|atob\()\b|[A-Za-z0-9+/]{120,}={0,2}", _I),
    ),
    (
        "instruction_voice",
        Severity.MEDIUM,
        re.compile(
            r"\b(?:you (?:must|should|have to|need to|will)|always|only)\b.{0,30}\b(?:respond|"
            r"answer|reply)\b(?: with| only| in)\b",
            _I,
        ),
    ),
    (
        "hidden_characters",
        Severity.MEDIUM,
        re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]{3,}"),
    ),
]


def normalise(text: str) -> str:
    """Fold look-alike characters and drop invisible ones, so obfuscation cannot hide a phrase."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _ZERO_WIDTH.sub("", text)
    return _SPACES.sub(" ", text).strip()  # newlines stay: some rules are line-anchored


def scan(text: str) -> list[Finding]:
    """Every rule that matched, most severe first. Empty means nothing suspicious was found."""
    if not text:
        return []
    found: list[Finding] = []
    raw, folded = text, normalise(text)
    for name, severity, pattern in _RULES:
        # hidden characters are only visible in the raw text; the rest are matched on folded text
        if pattern.search(raw if name == "hidden_characters" else folded):
            found.append(Finding(name, severity))
    return sorted(found, key=lambda f: -f.severity)


def worst(findings: list[Finding]) -> Severity:
    return max((f.severity for f in findings), default=Severity.NONE)
