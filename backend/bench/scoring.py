"""Deterministic answer checkers for the intelligence benchmark. No model grades another model."""

from __future__ import annotations

import json
import re

HEDGES = (
    "don't know", "do not know", "not sure", "cannot", "can't", "no way to", "no record",
    "unable", "not able", "no information", "no access", "don't have", "do not have",
    "haven't", "have not", "not aware", "not familiar", "unknown", "no knowledge",
    "impossible to", "no data", "not told", "uncertain", "speculat", "not know",
    "no means", "not possible", "isn't something i", "no reliable",
)  # fmt: skip
_WORDS = {
    **{w: i for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split())},
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}  # fmt: skip
_NUM = re.compile(r"-?\d[\d,]*\.?\d*|\b(?:" + "|".join(_WORDS) + r")\b", re.IGNORECASE)


def _numbers(text: str) -> list[float]:
    """Every number in reading order, written as digits ("1,234.5") or as a word ("three")."""
    out = []
    for m in _NUM.findall(text):
        if m.lower() in _WORDS:
            out.append(float(_WORDS[m.lower()]))
            continue
        try:
            out.append(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            continue
    return out


_LEADING_NUMBER = re.compile(r"^[\s$*_`(]*-?\d[\d,]*\.?\d*(?=[\s.,;:)*_`]|$)")


def _clean(text: str) -> str:
    return text.strip().strip("`*_\"' \n").strip()


def _fenced(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (m.group(1) if m else text).strip()


_FINAL_LINE_KINDS = {
    "number", "exact", "word_list", "json_keys", "no_letter", "all_words_start", "word_position",
}  # fmt: skip
_FINAL = re.compile(r"(?:^|\n)\W*(?:final )?answer\s*[:=-]\s*", re.IGNORECASE)


def final_answer(reply: str) -> str:
    """If the reply works through the problem and ends with "Answer: X", score X; else the reply."""
    parts = _FINAL.split(reply)
    tail = parts[-1].strip() if len(parts) > 1 else reply.strip()
    return tail or reply.strip()


def check(spec: dict, reply: str) -> bool:
    kind = spec["type"]
    # Format-sensitive checks read the final "Answer:" line; "does the reply say X" checks read it all.
    text = final_answer(reply) if kind in _FINAL_LINE_KINDS else reply.strip()
    low = text.lower()
    if kind == "number":
        # The answer is the LAST number in the reply, or, when the reply leads with the answer
        # ("42. The sequence follows ..."), the only number in its first sentence.
        tol = spec.get("tol", 1e-6)
        nums = _numbers(text)
        if nums and abs(nums[-1] - spec["expected"]) <= tol:
            return True
        lead = _LEADING_NUMBER.match(text)  # "42 is next because ..." starts with the answer
        if lead and abs(_numbers(lead.group())[0] - spec["expected"]) <= tol:
            return True
        first = _numbers(re.split(r"(?<=[.!?:])\s+(?=[A-Z\n(])", text, maxsplit=1)[0])
        return len(first) == 1 and abs(first[0] - spec["expected"]) <= tol
    if kind == "exact":
        return re.fullmatch(spec["pattern"], _clean(low).rstrip(".!"), re.IGNORECASE) is not None
    if kind == "contains_all":
        return all(s.lower() in low for s in spec["items"])
    if kind == "contains_any":
        return any(s.lower() in low for s in spec["items"])
    if kind == "word_list":  # exactly N comma-separated single words, nothing else
        parts = [p.strip() for p in _clean(text).rstrip(".").split(",")]
        return len(parts) == spec["n"] and all(re.fullmatch(r"[A-Za-z'-]+", p) for p in parts)
    if kind == "json_keys":
        try:
            data = json.loads(_fenced(text))
        except ValueError:
            return False
        types = {"string": str, "number": (int, float)}
        return isinstance(data, dict) and all(
            k in data and isinstance(data[k], types[t]) and not isinstance(data[k], bool)
            for k, t in spec["keys"].items()
        )
    if kind == "no_letter":
        words = re.findall(r"[A-Za-z']+", text)
        return len(words) >= spec.get("min_words", 3) and spec["letter"].lower() not in low
    if kind == "all_words_start":
        words = re.findall(r"[A-Za-z']+", text)
        return len(words) >= spec.get("min_words", 3) and all(
            w.lower().startswith(spec["letter"].lower()) for w in words
        )
    if (
        kind == "first_mention"
    ):  # the first candidate named is THE answer ("Jupiter... Saturn" fails)
        hits = [(low.find(c.lower()), c.lower()) for c in spec["candidates"] if c.lower() in low]
        return bool(hits) and min(hits)[1] == spec["expected"].lower()
    if kind == "word_position":  # exactly n words, and word number `position` is `word`
        words = re.findall(r"[A-Za-z']+", text)
        return (
            len(words) == spec["n"] and words[spec["position"] - 1].lower() == spec["word"].lower()
        )
    if kind == "hedge":
        return any(h in low for h in HEDGES)
    raise ValueError(f"unknown check type {kind!r}")


def load(path) -> list[dict]:
    items = json.loads(open(path, encoding="utf-8").read())
    ids = [q["id"] for q in items]
    assert len(ids) == len(set(ids)), "duplicate question ids"
    return items
