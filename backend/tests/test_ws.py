"""WebSocket chat protocol (shared/protocols/chat_ws.md). Fake engines, real app and database."""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.models.base import ProviderUnavailable
from tests.conftest import AUTH, make_settings
from tests.fakes import StreamingFake

URL = "/v1/ws/chat"


@contextmanager
def server(tmp_path, cloud=None, local=None, **settings):
    app = create_app(make_settings(tmp_path, **settings), cloud=cloud, local=local)
    with TestClient(app) as client:
        yield client


def recv_until(ws, kinds=("end", "error")):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in kinds:
            return events


def chat(ws, message="hi", **extra):
    ws.send_json({"type": "chat", "message": message, **extra})


def conversation(client, cid):
    return client.get(f"/v1/conversations/{cid}", headers=AUTH).json()


def test_rejects_missing_and_wrong_credentials(tmp_path):
    with server(tmp_path, local=StreamingFake("local")) as c:
        for headers in ({}, {"Authorization": "Bearer " + "x" * 32}):
            with pytest.raises(WebSocketDisconnect) as info:
                with c.websocket_connect(URL, headers=headers):
                    pass
            assert info.value.code == 1008


def test_token_in_query_string_is_not_accepted(tmp_path):
    from tests.conftest import TOKEN  # noqa: PLC0415

    with server(tmp_path, local=StreamingFake("local")) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"{URL}?token={TOKEN}"):
                pass


def test_streams_start_deltas_end_and_persists(tmp_path):
    local = StreamingFake("local", model="qwen")
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "hello")
        events = recv_until(ws)
        kinds = [e["type"] for e in events]
        assert kinds == ["start", "delta", "delta", "delta", "end"]
        assert events[0]["engine"] == {"provider": "fake-local", "model": "qwen", "tier": "local"}
        assert "".join(e["text"] for e in events if e["type"] == "delta") == "Hello there. Done."
        end = events[-1]
        assert end["interrupted"] is False and end["reply"] == "Hello there. Done."
        assert end["usage"]["completion_tokens"] == 5
        saved = conversation(c, end["conversation_id"])
        assert [(m["role"], m["content"]) for m in saved["messages"]] == [
            ("user", "hello"),
            ("assistant", "Hello there. Done."),
        ]


def test_two_turns_on_one_connection_share_history(tmp_path):
    local = StreamingFake("local")
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "first")
        cid = recv_until(ws)[-1]["conversation_id"]
        chat(ws, "second", conversation_id=cid)
        recv_until(ws)
        sent = [(m.role, m.content) for m in local.requests[1].messages[1:]]
        assert sent == [("user", "first"), ("assistant", "Hello there. Done."), ("user", "second")]


def test_voice_modality_uses_spoken_prompt_and_token_cap(tmp_path):
    local = StreamingFake("local")
    with server(tmp_path, local=local, voice_max_output_tokens=123) as c:
        with c.websocket_connect(URL, headers=AUTH) as ws:
            chat(ws, "hi", modality="voice")
            recv_until(ws)
            chat(ws, "hi", modality="text")
            recv_until(ws)
    voice, text = local.requests
    assert "SPOKEN ALOUD" in voice.messages[0].content and voice.max_output_tokens == 123
    assert "SPOKEN ALOUD" not in text.messages[0].content and text.max_output_tokens == 2048


def test_cancel_mid_stream_saves_only_what_was_spoken(tmp_path):
    local = StreamingFake("local", chunks=tuple(f"Sentence {i}. " for i in range(40)), delay=0.05)
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "talk at length")
        assert ws.receive_json()["type"] == "start"
        ws.receive_json()  # one delta heard
        ws.send_json({"type": "cancel", "spoken": "Sentence 0."})
        end = None
        while True:
            ev = ws.receive_json()
            if ev["type"] == "end":
                end = ev
                break
        assert end["interrupted"] is True and end["reply"] == "Sentence 0."
        saved = conversation(c, end["conversation_id"])
        assert saved["messages"][1]["content"] == "Sentence 0."
        assert saved["messages"][1]["route"]["interrupted"] is True
        assert local.closed  # upstream generation was stopped, not left running


def test_cancel_without_spoken_text_saves_the_partial_reply(tmp_path):
    local = StreamingFake("local", chunks=("Alpha. ", "Beta. ", "Gamma. ", "Delta. "), delay=0.15)
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "go")
        recv_n = 0
        while recv_n < 2:  # start + first delta
            ws.receive_json()
            recv_n += 1
        ws.send_json({"type": "cancel"})
        end = recv_until(ws, ("end",))[-1]
        assert end["interrupted"] is True
        assert end["reply"].startswith("Alpha.") and "Delta" not in end["reply"]


def test_cancel_before_any_text_saves_nothing(tmp_path):
    local = StreamingFake("local", delay=5.0)  # first chunk would take 5 s
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "slow start")
        time.sleep(0.2)
        ws.send_json({"type": "cancel"})
        end = recv_until(ws, ("end",))[-1]
        assert end["interrupted"] is True and end["message_id"] is None and end["reply"] == ""
        assert c.get("/v1/conversations", headers=AUTH).json() == []


def test_second_chat_while_busy_is_refused_then_connection_recovers(tmp_path):
    local = StreamingFake("local", delay=0.2)
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "one")
        chat(ws, "two")
        evs = []
        while True:
            ev = ws.receive_json()
            evs.append(ev)
            if ev["type"] == "end":
                break
        assert any(e["type"] == "error" and e["code"] == "busy" for e in evs)
        chat(ws, "three")
        assert recv_until(ws)[-1]["type"] == "end"


@pytest.mark.parametrize(
    "frame",
    [
        "not json",
        "[1,2]",
        '{"type": "bogus"}',
        '{"type": "chat"}',
        '{"type": "chat", "message": ""}',
        '{"type": "chat", "message": "x", "privacy": "public"}',
        '{"type": "chat", "message": "x", "conversation_id": "nope"}',
    ],
)
def test_bad_frames_yield_errors_without_closing(tmp_path, frame):
    with (
        server(tmp_path, local=StreamingFake("local")) as c,
        c.websocket_connect(URL, headers=AUTH) as ws,
    ):
        ws.send_text(frame)
        assert ws.receive_json()["type"] == "error"
        chat(ws, "still alive")
        assert recv_until(ws)[-1]["type"] == "end"


def test_oversized_frame_rejected(tmp_path):
    with (
        server(tmp_path, local=StreamingFake("local")) as c,
        c.websocket_connect(URL, headers=AUTH) as ws,
    ):
        ws.send_text("x" * (64 * 1024 + 1))
        assert ws.receive_json()["code"] == "too_large"


def test_unknown_conversation_is_an_error_event(tmp_path):
    local = StreamingFake("local")
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "hi", conversation_id="0" * 36)
        assert recv_until(ws)[-1]["code"] == "not_found"
        assert local.requests == []


def test_all_engines_down_is_an_error_and_saves_nothing(tmp_path):
    down = ProviderUnavailable("x")
    with server(
        tmp_path, StreamingFake("cloud", error=down), StreamingFake("local", error=down)
    ) as c:
        with c.websocket_connect(URL, headers=AUTH) as ws:
            chat(ws, "Analyze this plan")
            err = recv_until(ws)[-1]
            assert err["code"] == "models_unavailable" and len(err["attempts"]) == 2
        assert c.get("/v1/conversations", headers=AUTH).json() == []


def test_cloud_down_before_output_falls_back_with_notice(tmp_path):
    cloud = StreamingFake("cloud", error=ProviderUnavailable("x"))
    with (
        server(tmp_path, cloud, StreamingFake("local")) as c,
        c.websocket_connect(URL, headers=AUTH) as ws,
    ):
        chat(ws, "Analyze the strategic tradeoffs of this plan")
        events = recv_until(ws)
        assert (
            events[0]["fallback"]
            and events[0]["degraded"]
            and "less capable" in events[0]["notice"]
        )
        assert events[-1]["type"] == "end"


def test_mid_stream_failure_saves_partial_and_reports_it(tmp_path):
    local = StreamingFake("local", error=ProviderUnavailable("boom"), fail_at=2)
    with server(tmp_path, local=local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "hi")
        events = recv_until(ws)
        err = events[-1]
        assert err["code"] == "stream_failed" and err["partial"] is True
        saved = conversation(c, err["conversation_id"])
        assert saved["messages"][1]["content"] == "Hello there."
        assert saved["messages"][1]["route"]["error"] == "unavailable"


def test_local_only_lock_applies_over_websocket(tmp_path):
    cloud, local = StreamingFake("cloud"), StreamingFake("local")
    with server(tmp_path, cloud, local) as c, c.websocket_connect(URL, headers=AUTH) as ws:
        chat(ws, "private", privacy="local_only")
        cid = recv_until(ws)[-1]["conversation_id"]
        chat(ws, "Analyze the strategic tradeoffs of this plan", conversation_id=cid)
        events = recv_until(ws)
    assert events[0]["engine"]["tier"] == "local" and events[0]["privacy_locked"] is True
    assert "local-only" in events[0]["notice"]
    assert cloud.requests == []


def test_rate_limit_applies_to_chat_frames(tmp_path):
    with server(tmp_path, local=StreamingFake("local"), rate_limit_per_minute=2) as c:
        with c.websocket_connect(URL, headers=AUTH) as ws:
            for _ in range(2):
                chat(ws, "x")
                recv_until(ws)
            chat(ws, "x")
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == "rate_limited"


def test_client_disconnect_mid_stream_stops_generation_and_keeps_partial(tmp_path):
    local = StreamingFake("local", chunks=tuple(f"S{i}. " for i in range(100)), delay=0.05)
    with server(tmp_path, local=local) as c:
        with c.websocket_connect(URL, headers=AUTH) as ws:
            chat(ws, "stream a lot")
            ws.receive_json()
            ws.receive_json()
        deadline = time.time() + 5
        convs = []
        while time.time() < deadline and not (
            convs := c.get("/v1/conversations", headers=AUTH).json()
        ):
            time.sleep(0.05)
        assert convs, "partial reply was not persisted after disconnect"
        assert local.closed
        msgs = conversation(c, convs[0]["id"])["messages"]
        assert msgs[1]["route"]["interrupted"] is True and msgs[1]["content"].startswith("S0.")


def test_late_cancel_amends_the_stored_reply_to_what_was_heard(tmp_path):
    """The model finished before the operator interrupted: history must match what was heard."""
    with (
        server(tmp_path, local=StreamingFake("local")) as c,
        c.websocket_connect(URL, headers=AUTH) as ws,
    ):
        chat(ws, "hello")
        end = recv_until(ws)[-1]
        assert end["reply"] == "Hello there. Done."
        ws.send_json({"type": "cancel", "message_id": end["message_id"], "spoken": "Hello there."})
        ev = ws.receive_json()
        assert ev == {"type": "amended", "message_id": end["message_id"], "reply": "Hello there."}
        saved = conversation(c, end["conversation_id"])["messages"][1]
        assert saved["content"] == "Hello there."
        assert saved["route"]["interrupted"] is True and saved["route"]["amended"] is True
        assert saved["route"]["generated_chars"] == len("Hello there. Done.")
        chat(ws, "again", conversation_id=end["conversation_id"])  # connection still healthy
        assert recv_until(ws)[-1]["type"] == "end"


def test_late_cancel_with_nothing_heard_removes_the_reply_but_keeps_the_question(tmp_path):
    with (
        server(tmp_path, local=StreamingFake("local")) as c,
        c.websocket_connect(URL, headers=AUTH) as ws,
    ):
        chat(ws, "hello")
        end = recv_until(ws)[-1]
        ws.send_json({"type": "cancel", "message_id": end["message_id"], "spoken": ""})
        assert ws.receive_json() == {
            "type": "amended",
            "message_id": end["message_id"],
            "reply": "",
        }
        msgs = conversation(c, end["conversation_id"])["messages"]
        assert [(m["role"], m["content"]) for m in msgs] == [("user", "hello")]


def test_late_cancel_for_unknown_or_user_message_is_an_error(tmp_path):
    with (
        server(tmp_path, local=StreamingFake("local")) as c,
        c.websocket_connect(URL, headers=AUTH) as ws,
    ):
        ws.send_json({"type": "cancel", "message_id": 99999, "spoken": "x"})
        assert ws.receive_json()["code"] == "not_found"
        chat(ws, "hello")
        end = recv_until(ws)[-1]
        user_id = end["message_id"] - 1
        ws.send_json({"type": "cancel", "message_id": user_id, "spoken": "x"})
        assert ws.receive_json()["code"] == "not_found"  # cannot rewrite the operator's own words
        assert conversation(c, end["conversation_id"])["messages"][0]["content"] == "hello"


def test_style_rules_hold_over_the_socket_and_in_the_stored_reply(tmp_path):
    """Emoji and '!' from the model never reach the client, history, or the stored reply."""
    chunks = ("No. Cheer", "fulness is noise", "! ", "🚀✨ Ask", " now", "!")
    with server(tmp_path, local=StreamingFake("local", chunks=chunks)) as c:
        with c.websocket_connect(URL, headers=AUTH) as ws:
            chat(ws, "be cheerful with emoji")
            events = recv_until(ws)
        deltas = "".join(e["text"] for e in events if e["type"] == "delta")
        end = events[-1]
        assert deltas.strip() == end["reply"] == "No. Cheerfulness is noise.  Ask now."
        assert not any(ch in deltas for ch in "🚀✨!")
        stored = conversation(c, end["conversation_id"])["messages"][-1]["content"]
        assert stored == end["reply"]
