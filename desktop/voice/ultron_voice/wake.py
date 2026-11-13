"""Wake word handling.

Two interchangeable strategies (see docs/PHASE_2_VOICE.md for measured numbers):

* STT keyword spotting: VAD finds an utterance, whisper.cpp transcribes it, `match_wake` looks for
  "ULTRON" near the start. Works today with no trained model; costs CPU on every utterance heard
  while on standby, and the transcript of non-wake speech is discarded immediately.
* openWakeWord: a small always-on acoustic model (`OpenWakeWordDetector`). Needs a custom-trained
  "ultron" model file; none ships with openWakeWord.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

WAKE = "ultron"
# Accepted only as the first words of an utterance, e.g. "hey ultron, ...".
_LEAD_INS = {"hey", "hi", "hello", "okay", "ok", "yo", "oh"}
_PUNCT = re.compile(r"[^\w\s']")


@dataclass(frozen=True)
class WakeMatch:
    heard: str  # the token(s) that matched, as transcribed
    command: str  # what followed the wake word in the same breath ("" if nothing)
    score: float


def _norm(text: str) -> list[str]:
    return _PUNCT.sub(" ", text.lower()).split()


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# What a recogniser writes for "Ultron": a vowel, L, T, R, up to three vowels/h, N. This is the
# consonant skeleton L-T-R-N, so "altron", "oltron", "ultrahn", "ultran" pass while "ultra",
# "cutron", "retron", "electron", "neutron" and "alderon" (wrong start or no L-T-R-N) do not.
_SKELETON = re.compile(r"^[uao][aeiou]?l+t+r[aeiouh]{0,3}n$")


def _score(cand: str, name: str = WAKE) -> float:
    if name == WAKE and _SKELETON.match(cand):
        return max(_sim(cand, WAKE), 0.9)
    return _sim(cand, name)


def match_wake(
    transcript: str, *, name: str = WAKE, threshold: float | None = None, max_lead: int = 2
) -> WakeMatch | None:
    """Find the wake name in the first few words. Tolerates ASR spellings ('ultrahn', 'ul tron').

    Deliberately strict about position: a mid-sentence mention ("the movie Ultron was ...") must not
    wake the assistant.
    """
    name = name.lower()
    if threshold is None:
        threshold = 0.84 if name == WAKE else 0.80
    words = _norm(transcript)
    for start in range(0, min(len(words), max_lead + 1)):
        if start and words[start - 1] not in _LEAD_INS:
            break  # only greetings may precede the wake word
        for span in (1, 2):  # "ultron" or a split like "ul tron" / "ultra on"
            if start + span > len(words):
                continue
            cand = "".join(words[start : start + span])
            score = _score(cand, name)
            if score >= threshold:
                rest = " ".join(words[start + span :])
                return WakeMatch(" ".join(words[start : start + span]), rest, score)
    return None


_STANDBY = re.compile(r"^(?:ok(?:ay)?\s+|please\s+)?stand\s*-?\s*by\b|^go to (?:sleep|standby)\b")


def is_standby(transcript: str) -> bool:
    return bool(_STANDBY.match(" ".join(_norm(transcript))))


class OpenWakeWordDetector:
    """Frame-based acoustic wake word via openWakeWord. Requires a trained model file."""

    def __init__(self, model_path: str | Path, *, threshold: float = 0.5) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"wake word model not found: {path}. openWakeWord ships no 'ultron' model; "
                "train one (docs/PHASE_2_VOICE.md) or use the STT keyword strategy."
            )
        from openwakeword.model import Model  # noqa: PLC0415 - optional dependency

        self._model = Model(wakeword_models=[str(path)], inference_framework="onnx")
        self._name = next(iter(self._model.models))
        self.threshold = threshold

    def reset(self) -> None:
        self._model.reset()

    def score(self, frame: np.ndarray) -> float:
        """Feed 80 ms-multiples of int16 16 kHz audio; returns the latest wake score."""
        return float(self._model.predict(frame)[self._name])
