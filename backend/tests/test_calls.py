"""Calls on command: ULTRON asks the phone to confirm; it never dials and never sees contacts."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.devices.calls import CallTarget, parse_call
from app.main import create_app
from tests.conftest import AUTH, make_settings
from tests.fakes import FakeAdapter, StreamingFake
from tests.test_devices import PUSH, bearer, pair_device, pair_sync

COMMANDS = [
    ("call Asha", CallTarget("name", "Asha")),
    ("Call Asha.", CallTarget("name", "Asha")),
    ("please call Asha Rao", CallTarget("name", "Asha Rao")),
    ("Ultron, phone Dr. Patel", CallTarget("name", "Dr. Patel")),
    ("could you ring mum?", CallTarget("name", "mum")),
    ("dial 555 0100", CallTarget("number", "5550100")),
    ("call +44 20 7946 0958", CallTarget("number", "+442079460958")),
    ("call José", CallTarget("name", "José")),
]
NOT_COMMANDS = [
    "call me a taxi",
    "let's call it a day",
    "call me later",
    "call back",
    "who should I call about a broken boiler",
    "I will call Asha tomorrow",  # a statement, not a command
    "call Asha and tell her I am late",  # more than a bare target: not taken as a command
    "call",
    "call 12",
    "call " + "x" * 80,
    "call Asha; rm -rf /",
    "call <script>",
    "What is a phone call?",
    "ring the bell",
]


@pytest.mark.parametrize(("text", "expected"), COMMANDS)
def test_call_commands_are_recognised(text, expected):
    assert parse_call(text) == expected


@pytest.mark.parametrize("text", NOT_COMMANDS)
def test_ordinary_sentences_are_not_call_commands(text):
    assert parse_call(text) is None, text


def prompt_of(adapter) -> str:
    return adapter.requests[-1].messages[0].content


async def test_a_call_command_asks_the_phone_and_tells_the_model_it_cannot_dial(run_app):
    local = FakeAdapter("local", reply="Your phone is asking you to confirm.")
    async with run_app(None, local) as (c, app):
        dev = await pair_device(c, ["chat", "notify", "call"])
        r = await c.post("/v1/chat", json={"message": "call Asha"}, headers=AUTH)
        out = r.json()
        action = out["actions"][0]
        assert action["type"] == "call" and action["target"] == "Asha"
        assert action["status"] == "awaiting_confirmation" and action["devices"] == 1
        pending = await app.state.services.hub.pending(dev["device_id"])
        assert [p["kind"] for p in pending] == ["call_proposal"]
        assert pending[0]["payload"]["target"] == "Asha"
        assert pending[0]["payload"]["request_id"] == action["request_id"]
        note = prompt_of(local)
        assert "nothing is dialled until they tap Confirm" in note and "cannot dial" in note


async def test_without_a_phone_that_may_call_nothing_is_sent_and_the_model_says_so(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, app):
        dev = await pair_device(c, ["chat", "notify"])  # no `call`
        out = (await c.post("/v1/chat", json={"message": "call Asha"}, headers=AUTH)).json()
        assert out["actions"][0]["status"] == "no_phone" and out["actions"][0]["devices"] == 0
        assert await app.state.services.hub.pending(dev["device_id"]) == []
        assert "no phone with calling permission" in prompt_of(local)


async def test_ordinary_chat_has_no_actions_and_no_note(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        await pair_device(c, ["chat", "notify", "call"])
        out = (await c.post("/v1/chat", json={"message": "call me a taxi"}, headers=AUTH)).json()
    assert out["actions"] == [] and "Action requested" not in prompt_of(local)


async def test_a_revoked_phone_is_not_asked(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        dev = await pair_device(c, ["chat", "notify", "call"])
        await c.delete(f"/v1/devices/{dev['device_id']}", headers=AUTH)
        out = (await c.post("/v1/chat", json={"message": "call Asha"}, headers=AUTH)).json()
        assert out["actions"][0]["status"] == "no_phone"


async def test_the_audit_record_keeps_the_action_but_the_phone_gets_only_the_name(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        await pair_device(c, ["chat", "notify", "call"])
        out = (await c.post("/v1/chat", json={"message": "call Asha"}, headers=AUTH)).json()
        msgs = (await c.get(f"/v1/conversations/{out['conversation_id']}", headers=AUTH)).json()
    assert msgs["messages"][-1]["route"]["actions"][0]["target"] == "Asha"


def test_the_proposal_and_the_streamed_start_frame_reach_the_phone_and_the_caller(tmp_path):
    app = create_app(make_settings(tmp_path), local=StreamingFake("local"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        dev = pair_sync(client, ["chat", "notify", "call"])
        with client.websocket_connect(PUSH, headers=bearer(dev["token"])) as phone:
            with client.websocket_connect("/v1/ws/chat", headers=AUTH) as chat:
                chat.send_json({"type": "chat", "message": "call Asha"})
                events = []
                while not events or events[-1]["type"] not in ("end", "error"):
                    events.append(chat.receive_json())
            start = next(e for e in events if e["type"] == "start")
            assert start["actions"][0]["status"] == "awaiting_confirmation"
            push = phone.receive_json()
            assert push["kind"] == "call_proposal" and push["payload"]["target"] == "Asha"
