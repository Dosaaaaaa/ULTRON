"""Reminders by chat: the time is parsed in plain code and never guessed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.devices.remind_cmd import Parsed, Unclear, parse_reminder
from app.models.base import ProviderUnavailable
from tests.conftest import AUTH
from tests.fakes import FakeAdapter
from tests.test_devices import pair_device

# Thursday 12 Nov 2026, 15:00 in a +05:30 zone
NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def at(day: str, hh: int, mm: int = 0) -> datetime:
    d = {"thu": 12, "fri": 13, "sat": 14}[day]
    return datetime(2026, 11, d, hh, mm, tzinfo=NOW.tzinfo)


GOOD = [
    (
        "remind me to call the dentist in 30 minutes",
        "call the dentist",
        NOW + timedelta(minutes=30),
    ),
    ("Remind me in an hour to take the pizza out", "take the pizza out", NOW + timedelta(hours=1)),
    ("remind me in half an hour to stretch", "stretch", NOW + timedelta(minutes=30)),
    ("remind me to drink water in two hours", "drink water", NOW + timedelta(hours=2)),
    ("please remind me at 5:30 pm to take the bins out", "take the bins out", at("thu", 17, 30)),
    ("remind me tomorrow at 9am about the invoice", "the invoice", at("fri", 9)),
    ("remind me on friday at 8 am to submit", "submit", at("fri", 8)),
    ("set a reminder for 9am tomorrow: pay rent", "pay rent", at("fri", 9)),
    ("remind me 9pm to lock up", "lock up", at("thu", 21)),
    ("remind me at 21:15 to switch off the oven", "switch off the oven", at("thu", 21, 15)),
    ("remind me at noon to eat", "eat", at("fri", 12)),  # noon has passed today: the next one
    ("remind me to meet Bob at 5 pm at the cafe", "meet Bob at the cafe", at("thu", 17)),
    ("remind me to pay 3 bills in 2 days", "pay 3 bills", NOW + timedelta(days=2)),
    ("remind me to water the plants at 9 am", "water the plants", at("fri", 9)),
    ("remind me on thursday at 10am to call", "call", at("thu", 10) + timedelta(days=7)),
]


@pytest.mark.parametrize(("text", "what", "due"), GOOD)
def test_reminder_commands_are_understood(text, what, due):
    got = parse_reminder(text, NOW)
    assert isinstance(got, Parsed), got
    assert (got.text, got.due) == (what, due)


UNCLEAR = [
    "remind me at 5 to leave",  # 5 am or 5 pm? never guess
    "remind me to stretch",  # no time at all
    "remind me to call mum tomorrow",  # a day without a clock time
    "remind me today at 1pm to call",  # already passed
    "remind me at 25:00 to x",
    "remind me in 5 seconds to x",
    "remind me in half an hour",  # no subject
    "remind me at 13pm to x",
]


@pytest.mark.parametrize("text", UNCLEAR)
def test_an_unclear_reminder_creates_nothing_and_says_why(text):
    got = parse_reminder(text, NOW)
    assert isinstance(got, Unclear) and got.why, text


NOT_COMMANDS = [
    "Do you remind me of someone?",
    "what should I be reminded of tomorrow",
    "The reminder app is broken",
    "I set a reminder yesterday",
    "tell me how to remind me",
    "",
    "remind",
]


@pytest.mark.parametrize("text", NOT_COMMANDS)
def test_ordinary_sentences_are_not_reminder_commands(text):
    assert parse_reminder(text, NOW) is None, text


def test_the_text_is_capped_and_cleaned():
    got = parse_reminder("remind me in 10 minutes to " + "very " * 200 + "important", NOW)
    assert isinstance(got, Parsed) and len(got.text) == 300


def prompt_of(adapter) -> str:
    return adapter.requests[-1].messages[0].content


async def test_a_reminder_command_sets_the_reminder_and_the_model_confirms_it(run_app):
    local = FakeAdapter("local", reply="Done.")
    async with run_app(None, local) as (c, _):
        await pair_device(c)
        out = (
            await c.post(
                "/v1/chat",
                json={"message": "remind me in 20 minutes to call the dentist"},
                headers=AUTH,
            )
        ).json()
        action = out["actions"][0]
        assert action["type"] == "reminder" and action["status"] == "created"
        assert action["text"] == "call the dentist" and action["devices"] == 1
        listed = (await c.get("/v1/reminders", headers=AUTH)).json()
        assert [r["text"] for r in listed] == ["call the dentist"]
        due = datetime.fromisoformat(listed[0]["due_at"])
        assert abs((due - (datetime.now(UTC) + timedelta(minutes=20))).total_seconds()) < 30
        assert "A reminder was set" in prompt_of(local) and "call the dentist" in prompt_of(local)
        # the model is given the operator's local time and distance, never a UTC timestamp
        assert "in 20 minutes" in prompt_of(local) and "+00:00" not in prompt_of(local)
        assert "in 20 minutes" in action["due_local"]


async def test_an_unclear_reminder_is_not_created_and_the_model_is_told_to_say_so(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        out = (
            await c.post("/v1/chat", json={"message": "remind me at 5 to leave"}, headers=AUTH)
        ).json()
        assert out["actions"][0]["status"] == "unclear"
        assert (await c.get("/v1/reminders", headers=AUTH)).json() == []
        assert "NO reminder was set" in prompt_of(local)


async def test_a_failed_turn_leaves_no_reminder_behind(run_app):
    down = FakeAdapter("local", error=ProviderUnavailable("down"))
    async with run_app(None, down) as (c, _):
        r = await c.post(
            "/v1/chat",
            json={"message": "remind me in 20 minutes to call the dentist"},
            headers=AUTH,
        )
        assert r.status_code == 503  # "Nothing was saved" has to stay true
        assert (await c.get("/v1/reminders", headers=AUTH)).json() == []


async def test_without_a_paired_phone_the_reminder_waits_and_the_model_says_so(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        out = (
            await c.post(
                "/v1/chat", json={"message": "remind me in 20 minutes to nap"}, headers=AUTH
            )
        ).json()
        assert out["actions"][0]["devices"] == 0
        assert "No phone is paired yet" in prompt_of(local)
        assert len((await c.get("/v1/reminders", headers=AUTH)).json()) == 1


async def test_a_call_and_a_reminder_commands_do_not_interfere(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        out = (await c.post("/v1/chat", json={"message": "call Asha"}, headers=AUTH)).json()
        assert [a["type"] for a in out["actions"]] == ["call"]
        out = (
            await c.post(
                "/v1/chat", json={"message": "remind me in 5 minutes to eat"}, headers=AUTH
            )
        ).json()
        assert [a["type"] for a in out["actions"]] == ["reminder"]


def test_the_time_is_given_in_plain_local_words():
    from app.devices.remind_cmd import local_words

    now = datetime(2026, 11, 12, 15, 0).astimezone()
    assert local_words(now + timedelta(minutes=1), now).endswith("(in 1 minute)")
    assert local_words(now + timedelta(minutes=45), now).startswith(
        (now + timedelta(minutes=45)).strftime("%H:%M")
    )
    assert "in 3 hours" in local_words(now + timedelta(hours=3), now)
    assert "tomorrow" in local_words(now + timedelta(hours=20), now)
    far = local_words(now + timedelta(days=3), now)
    assert "in 3 days" in far and " on " in far
