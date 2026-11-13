"""The benchmark's checkers: if these are wrong, every score built on them is wrong."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.scoring import check, load

BENCH = Path(__file__).resolve().parents[1] / "bench"
NUM = {"type": "number", "expected": 75, "tol": 0.01}


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        ("75", True),
        ("The average speed is 75 km/h.", True),
        ("Total distance 300 km over 4 hours = 75.0 km/h", True),
        ("About 70 km/h.", False),
        ("It is 75 km/h, not 80.", False),  # the LAST number is the answer: this one is 80
        ("No idea", False),
        ("", False),
    ],
)
def test_number_uses_the_last_number_in_the_reply(reply, ok):
    assert check(NUM, reply) is ok


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        (
            "42. The sequence follows n(n+1), where n is position 6.",
            True,
        ),  # answer first, then prose
        ("42 is next because 6 * 7 = 42 and the gaps grow by 2.", True),
        ("The answer: 42. Check: 6 x 7.", True),
        ("10. Wait, no: 42.", True),  # it corrects itself; the final number is right
        ("**42** is next, since 6 * 7 = 42 and gaps grow by 2.", True),
        ("$42 total, from 6 * 7 plus 0.", True),
        ("5 apples and 42 pears and 7 plums.", False),  # leads with a different number
        (
            "It is 42, not 5, and also 7.",
            False,
        ),  # a leading number only counts if it is the only one
        ("Probably 41. The pattern n(n+1) gives 42 only if n=6, but n=5.", False),
    ],
)
def test_a_reply_that_leads_with_the_answer_is_not_failed_for_what_follows(reply, ok):
    assert check({"type": "number", "expected": 42}, reply) is ok


def test_number_words_count_as_numbers():
    spec = {"type": "number", "expected": 3}
    assert check(spec, "The letter appears three times.")
    assert check(spec, "Three")
    assert not check(spec, "The letter appears four times.")
    assert check({"type": "number", "expected": 20}, "It is twenty metres per second.")
    assert check({"type": "number", "expected": 5}, "5 is the answer; not one, not ten, five.")
    assert not check({"type": "number", "expected": 5}, "The answer is five, not ten.")  # last wins


def test_number_handles_commas_dollars_and_decimals():
    assert check({"type": "number", "expected": 5050}, "The sum is 5,050.") is True
    assert check({"type": "number", "expected": 27.25, "tol": 0.01}, "Change: $27.25") is True
    assert check({"type": "number", "expected": 33, "tol": 0.01}, "$33.00") is True
    assert check({"type": "number", "expected": 391}, "17 x 23 = 391.") is True


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        ("Ben", True),
        ("  ben.  ", True),
        ("**Ben**", True),
        ("Ben is second.", False),
        ("Anna", False),
    ],
)
def test_exact_matches_the_whole_answer_only(reply, ok):
    assert check({"type": "exact", "pattern": "ben"}, reply) is ok


def test_exact_sequence_allows_any_spacing_but_nothing_else():
    spec = {"type": "exact", "pattern": r"2\s+3\s+5\s+7\s+11"}
    assert check(spec, "2 3 5 7 11") and check(spec, "2  3 5 7 11.")
    assert not check(spec, "2, 3, 5, 7, 11") and not check(spec, "The primes are 2 3 5 7 11")


def test_contains_checks():
    assert check(
        {"type": "contains_all", "items": ["quarter", "nickel"]}, "A Quarter and a nickel."
    )
    assert not check(
        {"type": "contains_all", "items": ["quarter", "nickel"]}, "Two dimes and a nickel"
    )
    assert check({"type": "contains_any", "items": ["Au"]}, "The symbol is Au.")
    assert not check({"type": "contains_any", "items": ["Au"]}, "Ag")


@pytest.mark.parametrize(
    ("reply", "n", "ok"),
    [
        ("salty, vast, deep", 3, True),
        ("salty,vast,deep.", 3, True),
        ("salty, vast", 3, False),
        ("salty, vast, deep, wide", 3, False),
        ("The sea is vast, deep, salty", 3, False),  # not nothing-else
        ("blue sea, vast, deep", 3, False),  # an item with two words
    ],
)
def test_word_list_is_exactly_n_single_words(reply, n, ok):
    assert check({"type": "word_list", "n": n}, reply) is ok


KEYS = {"type": "json_keys", "keys": {"name": "string", "age": "number"}}


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        ('{"name": "Ada", "age": 36}', True),
        ('```json\n{"name": "Ada", "age": 36.5}\n```', True),
        ('{"name": "Ada", "age": "36"}', False),  # age must be a number
        ('{"name": "Ada"}', False),
        ('{"name": 7, "age": 3}', False),
        ('{"name": "Ada", "age": true}', False),  # a bool is not a number here
        ("Sure! name is Ada", False),
        ("[1, 2]", False),
    ],
)
def test_json_keys(reply, ok):
    assert check(KEYS, reply) is ok


def test_letter_constraints():
    assert check(
        {"type": "no_letter", "letter": "e", "min_words": 3}, "Gray clouds drop cold rain on Troy."
    )
    assert not check({"type": "no_letter", "letter": "e", "min_words": 3}, "The rain falls softly.")
    assert not check(
        {"type": "no_letter", "letter": "e", "min_words": 3}, "Rain."
    )  # too short to count
    spec = {"type": "all_words_start", "letter": "m", "min_words": 4}
    assert check(spec, "Many mellow moons mirror mountains.")
    assert not check(spec, "Many mellow moons shine.")
    assert not check(spec, "Moon.")


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        ("I don't know what you ate.", True),
        ("I have no record of that.", True),
        ("I cannot predict future events.", True),
        ("It was pancakes.", False),
        ("Brazil won in 2038.", False),
    ],
)
def test_hedge_detects_honest_uncertainty_and_confident_invention(reply, ok):
    assert check({"type": "hedge"}, reply) is ok


def test_unknown_check_type_is_an_error_not_a_silent_pass():
    with pytest.raises(ValueError):
        check({"type": "vibes"}, "anything")


EASY_CATS = {"math", "logic", "instruction", "knowledge", "honesty", "code"}
HARD_CATS = {
    "arithmetic",
    "calendar_units",
    "combinatorics",
    "trap",
    "code",
    "counting",
    "false_premise",
}


@pytest.mark.parametrize(
    ("name", "size", "cats"),
    [
        ("dev.json", 24, EASY_CATS),
        ("held_out.json", 24, EASY_CATS),
        ("hard_dev.json", 20, HARD_CATS),
        ("hard_held.json", 20, HARD_CATS),
        (
            "hard_fresh.json",
            16,
            {"arithmetic", "calendar_units", "combinatorics", "trap", "code", "counting"},
        ),
    ],
)
def test_question_sets_are_well_formed(name, size, cats):
    items = load(BENCH / name)
    assert len(items) == size
    assert {q["category"] for q in items} == cats
    for q in items:
        assert q["question"].strip() and "type" in q["check"]
        check(q["check"], "probe")  # every spec is runnable (no unknown type / missing field)


def test_first_mention_catches_the_wrong_answer_that_names_the_right_one_later():
    spec = {
        "type": "first_mention",
        "candidates": ["saturn", "jupiter", "mars"],
        "expected": "saturn",
    }
    assert check(spec, "Saturn has the most known moons.")
    assert check(spec, "Saturn, ahead of Jupiter.")
    assert not check(
        spec, "Jupiter has the most moons. Saturn follows with 146."
    )  # the d17 false pass
    assert not check(spec, "I am not sure.")


def test_word_position_needs_the_exact_count_and_the_word_in_place():
    spec = {"type": "word_position", "n": 7, "position": 3, "word": "blue"}
    assert check(spec, "The tall blue house stood very still.")
    assert not check(spec, "The tall blue house stood still.")  # six words
    assert not check(spec, "The blue tall house stood very still.")  # blue is second


def test_no_question_appears_in_two_sets():
    sets = [
        load(BENCH / f"{n}.json")
        for n in ("dev", "held_out", "hard_dev", "hard_held", "hard_fresh")
    ]
    texts = [q["question"] for s in sets for q in s]
    ids = [q["id"] for s in sets for q in s]
    assert len(texts) == len(set(texts)) and len(ids) == len(set(ids))


KNOWN_GOOD = {  # a correct reply for every question, to prove the checkers can say yes
    "d01": "75 km/h", "d02": "391", "d03": "$33", "d04": "9 days", "d05": "48", "d06": "5050",
    "d07": "Ben", "d08": "A quarter and a nickel", "d09": "All 12", "d10": "9", "d11": "366",
    "d12": "vast, deep, salty", "d13": "Gray clouds drop cold rain on Troy",
    "d14": '{"name": "Ada", "age": 36}', "d15": "2 3 5 7 11", "d16": "Au", "d17": "Saturn",
    "d18": "1989", "d19": "Canberra", "d20": "I don't know", "d21": "I don't know", "d22": "I have no access to live data",
    "d23": "10", "d24": "4",
    "h01": "21", "h02": "80", "h03": "180", "h04": "4096", "h05": "10", "h06": "27.25",
    "h07": "Cy", "h08": "same", "h09": "5", "h10": "Tom", "h11": "6",
    "h12": "red, hot, bright, wild", "h13": "Many mellow moons mirror midnight mist",
    "h14": '{"city": "Veldt", "population": 12000}', "h15": "1 4 9 16 25 36", "h16": "Na",
    "h17": "Jane Austen", "h18": "Pacific", "h19": "212", "h20": "I don't know", "h21": "I cannot predict that",
    "h22": "I am not aware of that film", "h23": "6", "h24": "2",
    "x01": "17532", "x02": "11994", "x03": "423.2", "x04": "998001", "x05": "Friday", "x06": "138",
    "x07": "20 m/s", "x08": "30", "x09": "0.375", "x10": "66", "x11": "5", "x12": "42", "x13": "Alice",
    "x14": "18", "x15": "34", "x16": "hfdb", "x17": "3", "x18": "The tall blue house stood very still",
    "x19": "He won it for the photoelectric effect", "x20": "Apollo 18 never happened; it was cancelled.",
    "y01": "31344", "y02": "10844", "y03": "399.5", "y04": "7006652", "y05": "Thursday", "y06": "171",
    "y07": "12600", "y08": "60", "y09": "0.167", "y10": "360", "y11": "10", "y12": "48", "y13": "Finn",
    "y14": "12", "y15": "55", "y16": "beh", "y17": "5", "y18": "The old wooden green door creaked",
    "f01": "0.625", "f02": "29", "f03": "224", "f04": "16 km/h", "f05": "Wednesday", "f06": "137", "f07": "24",
    "f08": "0.111", "f09": "16", "f10": "70", "f11": "hlowrd", "f12": "4", "f13": "My red car runs fast", "f14": "Priya",
    "f15": "36", "f16": "32",
    "y19": "Her prizes were in Physics and Chemistry", "y20": "Apollo 19 never landed; it was cancelled.",
}  # fmt: skip


def test_a_correct_reply_passes_every_question_and_a_blank_reply_passes_none():
    names = ("dev", "held_out", "hard_dev", "hard_held", "hard_fresh")
    items = [q for n in names for q in load(BENCH / f"{n}.json")]
    assert {q["id"] for q in items} == set(KNOWN_GOOD)
    for q in items:
        assert check(q["check"], KNOWN_GOOD[q["id"]]), q["id"]
        assert not check(q["check"], "zzz"), q["id"]


def test_a_worked_reply_is_scored_on_its_final_answer_line():
    work = "Step 1: Thursday plus 100 days. 100 mod 7 = 2.\nStep 2: Wednesday + 2 = Friday.\nAnswer: Friday"
    assert check({"type": "exact", "pattern": "friday"}, work)
    assert not check({"type": "exact", "pattern": "friday"}, work.replace("Friday", "Thursday"))
    assert check(
        {"type": "number", "expected": 5},
        "Let x be the ball: 2x + 100 = 110, so x = 5.\nFinal answer: 5",
    )
    assert not check(
        {"type": "number", "expected": 5}, "x = 5, but I rechecked.\nAnswer: 10"
    )  # the final line wins
    assert check(
        {"type": "word_list", "n": 3}, "Thinking about the sea...\nAnswer: vast, deep, salty"
    )
    # without a marker the whole reply is scored as before
    assert check({"type": "exact", "pattern": "friday"}, "Friday")
    assert not check({"type": "exact", "pattern": "friday"}, "Work first. Friday")


def test_content_checks_read_the_whole_reply_not_just_the_answer_line():
    spec = {"type": "contains_any", "items": ["never", "cancel"]}
    assert check(spec, "Apollo 18 never occurred; the mission was cancelled.\nAnswer: N/A")
    assert check({"type": "hedge"}, "I cannot know that.\nAnswer: unknown to me")
    assert check(
        {"type": "contains_any", "items": ["photoelectric"]},
        "It was for the photoelectric effect.\nAnswer: 1921",
    )
    first = {"type": "first_mention", "candidates": ["saturn", "jupiter"], "expected": "saturn"}
    assert check(first, "Saturn, not Jupiter.\nAnswer: the ringed one")
