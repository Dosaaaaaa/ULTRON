"""A private reasoning pass for problem-like questions.

Small and mid-size models answer arithmetic, calendar, logic, code-tracing and counting questions
with the first thing that comes to mind. Measured on this project's benchmark (docs/INTELLIGENCE.md)
making the model work step by step first fixes a large share of those mistakes. This module decides
WHICH messages deserve that (a wrong trigger only slows ordinary chat down) and builds the two prompts:
the solver's, and the note that hands its working to the model that writes the final reply.

The solving pass goes through the normal router, so it obeys the same privacy rules: a local_only
conversation is solved on the device, and cloud tokens are spent only where cloud answers are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = r"\d"
_MATH_WORDS = re.compile(
    r"\b(multipl\w*|times|divid\w*|plus|minus|percent|per ?cent|sum|total|average|mean|median|"
    r"probabilit\w*|ways|arrange\w*|permutation\w*|combination\w*|handshakes?|how (?:many|much|long|far|fast)|"
    r"convert|per (?:hour|second|minute|day|litre|liter|kg)|square root|power of|remainder|"
    r"speed|discount\w*|interest|area|perimeter|volume|fraction|factorial|prime|squared|cubed)\b",
    re.IGNORECASE,
)
_EXPRESSION = re.compile(r"\d\s*(?:[-+*/x×÷^])\s*\d|\d\s*%\s*of\b|\bpercent of\b", re.IGNORECASE)
# combinatorics and probability read as problems even when the numbers are spelled out
_COUNTING_PROBLEM = re.compile(
    r"\bprobability (?:of|that)\b.{0,60}\b(?:flip\w*|roll\w*|draw\w*|pick\w*|toss\w*|choos\w*|select\w*|exactly)\b|"
    r"\bhow many (?:distinct |different |possible )?ways\b.{0,60}\b(?:letters|digits|numbers|arrang\w*|seat\w*|order\w*|choose|select\w*)\b|"
    r"\b(?:letters|digits|numbers|books|people|cards)\b.{0,40}\barranged\b|\bhandshakes?\b",
    re.IGNORECASE | re.DOTALL,
)
_DATES = re.compile(
    r"\b(day of the week|days? (?:after|before|from|between|until|since)|what day|what date|"
    r"weeks? (?:after|before|from)|leap year|how many days)\b",
    re.IGNORECASE,
)
_CODE = re.compile(
    r"```|\bwhat (?:does|will|would) (?:this|the following|that)\b.{0,40}\b(?:print|output|return|do)\b|"
    r"\b(?:trace|debug|dry run)\b.{0,30}\b(?:code|function|loop|program)\b",
    re.IGNORECASE | re.DOTALL,
)
_PUZZLE = re.compile(
    r"\b(riddle|puzzle|taller than|shorter than|older than|younger than|faster than|all but|"
    r"next (?:number|term|in the (?:sequence|series))|sequence|pattern|who (?:owns|finished|sits|lives)|"
    r"which (?:one )?(?:is|are) (?:heavier|lighter|taller|older)|if all|knights?|knaves?)\b",
    re.IGNORECASE,
)
_COUNT_FORMAT = re.compile(
    r"\bhow many (?:times|letters|words|vowels|consonants|characters|syllables)\b|"
    r"\bexactly (?:one|two|three|four|five|six|seven|eight|nine|ten|\d+) (?:words?|sentences?|letters?|items?)\b|"
    r"\b(?:does not|doesn't|without|never) (?:contain|use|include) the letter\b|"
    r"\bevery word (?:starts?|begins?) with\b|\bwhose (?:\w+ ){0,2}word is\b",
    re.IGNORECASE,
)
_VERBS = re.compile(
    r"\b(calculate|compute|solve|work out|figure out|prove|derive|evaluate the|simplify|"
    r"step by step|show your (?:work|working))\b",
    re.IGNORECASE,
)


def needs_reasoning(text: str) -> bool:
    """True for messages that are problems to solve rather than conversation."""
    t = text.strip()
    if len(t) < 8 or len(t) > 3000:
        return False
    if (
        _CODE.search(t)
        or _COUNT_FORMAT.search(t)
        or _PUZZLE.search(t)
        or _VERBS.search(t)
        or _COUNTING_PROBLEM.search(t)
    ):
        return True
    has_digit = re.search(_NUM, t) is not None
    if has_digit and (_EXPRESSION.search(t) or _MATH_WORDS.search(t) or _DATES.search(t)):
        return True
    return bool(_DATES.search(t))


SOLVER_SYSTEM = (
    "You are a careful problem solver. Solve the operator's problem, working step by step. "
    "Be exact: do arithmetic digit by digit, check dates by counting, trace code line by line, "
    "and verify any constraint (word counts, letters) by testing your answer against it. "
    "If a premise of the question is false, say so. After checking your result, finish with one "
    "last line of the form 'Answer: <the final answer only>'. Keep the working under 250 words."
)

_ANSWER = re.compile(r"(?:^|\n)\W*(?:final )?answer\s*[:=-]\s*(?P<a>.+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Working:
    text: str  # the solver's step-by-step working (shortened)
    answer: str | None  # the line after "Answer:", if it produced one
    tier: str  # which engine solved it

    def note(self) -> str:
        """What the answering model is told. Its working, never to be read out."""
        lines = [
            "# Private working for this question (your own step-by-step solution, made just now)",
            "Use it to give a correct, concise answer in your own voice and style. Do not read the "
            "working out or show it unless the operator asks. If your first instinct disagrees "
            "with the working, trust the working, but if you can see a clear mistake in it, say so "
            "and correct it.",
            self.text,
        ]
        if self.answer:
            lines.append(f"Best answer from the working: {self.answer}")
        return "\n".join(lines)


def parse_working(content: str, tier: str, limit: int = 1800) -> Working | None:
    text = content.strip()
    if not text:
        return None
    m = _ANSWER.search(text)
    return Working(text[:limit], m.group("a").strip() if m else None, tier)
