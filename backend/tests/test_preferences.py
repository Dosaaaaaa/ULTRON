"""The review switches: ask before dialling, and ask when a reminder time is unclear."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.devices.remind_cmd import Parsed, Unclear, parse_reminder
from tests.conftest import AUTH
from tests.fakes import FakeAdapter
from tests.test_devices import bearer, pair_device

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))  # Thu 15:00


def prompt_of(adapter) -> str:
    return adapter.requests[-1].messages[0].content


# ============================================================ the API
async def test_defaults_ask_before_dialling_and_ask_about_unclear_times(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        assert (await c.get("/v1/preferences", headers=AUTH)).json() == {
            "call_confirm": True,
            "reminder_guess": False,
        }


async def test_the_owner_and_a_chat_phone_can_change_them_others_cannot(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        r = await c.patch("/v1/preferences", json={"call_confirm": False}, headers=AUTH)
        assert r.json() == {"call_confirm": False, "reminder_guess": False}
        phone = bearer((await pair_device(c, ["chat", "notify"]))["token"])
        r = await c.patch("/v1/preferences", json={"reminder_guess": True}, headers=phone)
        assert r.json() == {"call_confirm": False, "reminder_guess": True}  # kept across callers
        assert (await c.get("/v1/preferences", headers=phone)).status_code == 200
        no_chat = bearer((await pair_device(c, ["notify"]))["token"])
        assert (await c.get("/v1/preferences", headers=no_chat)).status_code == 403
        assert (await c.patch("/v1/preferences", json={}, headers=no_chat)).status_code == 403
        assert (await c.get("/v1/preferences")).status_code == 401  # no credentials


@pytest.mark.parametrize(
    "body",
    [{"call_confirm": "yes"}, {"call_confirm": 1}, {"nope": True}, {"call_confirm": None, "x": 1}],
)
async def test_only_known_on_off_values_are_accepted(run_app, body):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        r = await c.patch("/v1/preferences", json=body, headers=AUTH)
        assert r.status_code == 422, body
        assert (await c.get("/v1/preferences", headers=AUTH)).json()["call_confirm"] is True


# ============================================================ calls
async def test_with_confirmation_off_the_phone_is_told_to_place_the_call_itself(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, app):
        dev = await pair_device(c, ["chat", "notify", "call"])
        await c.patch("/v1/preferences", json={"call_confirm": False}, headers=AUTH)
        out = (await c.post("/v1/chat", json={"message": "call 555 0100"}, headers=AUTH)).json()
        assert out["actions"][0]["status"] == "placing"
        (push,) = await app.state.services.hub.pending(dev["device_id"])
        assert push["payload"]["confirm"] is False and push["payload"]["target"] == "5550100"
        note = prompt_of(local)
        assert "turned call confirmation off" in note and "cannot dial" in note
        assert "Confirm" not in note


async def test_with_confirmation_on_nothing_changes(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        dev = await pair_device(c, ["chat", "notify", "call"])
        out = (await c.post("/v1/chat", json={"message": "call 555 0100"}, headers=AUTH)).json()
        assert out["actions"][0]["status"] == "awaiting_confirmation"
        (push,) = await app.state.services.hub.pending(dev["device_id"])
        assert push["payload"]["confirm"] is True


async def test_no_phone_is_still_reported_whatever_the_switch(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        await c.patch("/v1/preferences", json={"call_confirm": False}, headers=AUTH)
        out = (await c.post("/v1/chat", json={"message": "call 555 0100"}, headers=AUTH)).json()
        assert out["actions"][0]["status"] == "no_phone"


# ============================================================ reminders
GUESSED = [
    ("remind me tomorrow to call mum", "call mum", (13, 9, 0)),
    ("remind me on friday to submit", "submit", (13, 9, 0)),
    ("remind me tonight to lock up", "lock up", (12, 20, 0)),
    ("remind me at 5 to leave", "leave", (12, 17, 0)),  # 15:00 now: the next 5 is this evening
    ("remind me at 2 to eat", "eat", (13, 2, 0)),  # 14:00 has passed: the next 2 is tomorrow 02:00
    ("remind me tomorrow at 4 to call", "call", (13, 16, 0)),  # a day named: 1-6 means afternoon
    ("remind me tomorrow at 8 to run", "run", (13, 8, 0)),  # 7-11 means morning
    ("remind me at 7:30 to wake the kids", "wake the kids", (12, 19, 30)),  # next 7:30 is tonight
]


@pytest.mark.parametrize(("text", "what", "when"), GUESSED)
def test_with_guessing_on_a_vague_time_gets_a_sensible_default(text, what, when):
    got = parse_reminder(text, NOW, guess=True)
    day, hour, minute = when
    assert isinstance(got, Parsed), got
    assert got.text == what
    assert (got.due.day, got.due.hour, got.due.minute) == (day, hour, minute)


@pytest.mark.parametrize("text", [t for t, _, _ in GUESSED])
def test_with_guessing_off_the_same_phrases_still_ask(text):
    assert isinstance(parse_reminder(text, NOW, guess=False), Unclear)


@pytest.mark.parametrize(
    "text",
    [
        "remind me to stretch",  # no time at all: nothing to guess from
        "remind me today to call",  # "today" has no default time of day
        "remind me at 25:00 to x",
        "remind me in 5 seconds to x",
    ],
)
def test_even_when_guessing_a_missing_or_impossible_time_is_never_invented(text):
    assert isinstance(parse_reminder(text, NOW, guess=True), Unclear)


async def test_the_switch_changes_what_chat_does_with_an_unclear_time(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        msg = {"message": "remind me tomorrow to call mum"}
        first = (await c.post("/v1/chat", json=msg, headers=AUTH)).json()
        assert first["actions"][0]["status"] == "unclear"
        assert (await c.get("/v1/reminders", headers=AUTH)).json() == []
        await c.patch("/v1/preferences", json={"reminder_guess": True}, headers=AUTH)
        second = (await c.post("/v1/chat", json=msg, headers=AUTH)).json()
        assert second["actions"][0]["status"] == "created"
        (r,) = (await c.get("/v1/reminders", headers=AUTH)).json()
        assert (
            r["text"] == "call mum" and datetime.fromisoformat(r["due_at"]).astimezone().hour == 9
        )
