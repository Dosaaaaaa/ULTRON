"""Phone pairing, per-device tokens, the push socket with its outbox, and reminders."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import timedelta

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db.models import utcnow
from app.devices import reminders
from app.main import create_app
from tests.conftest import AUTH, TOKEN, make_settings
from tests.fakes import FakeAdapter, StreamingFake

PUSH = "/v1/ws/device"


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def pair_device(c, caps=None, name="Pixel"):
    body = {"capabilities": caps} if caps is not None else {}
    code = (await c.post("/v1/devices/pairing-code", json=body, headers=AUTH)).json()["code"]
    r = await c.post("/v1/devices/pair", json={"code": code, "name": name, "kind": "phone"})
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================ pairing
async def test_pairing_gives_the_device_its_own_token_shown_once(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        code = (await c.post("/v1/devices/pairing-code", json={}, headers=AUTH)).json()
        assert len(code["code"]) == 9 and code["code"][4] == "-"
        r = await c.post(
            "/v1/devices/pair", json={"code": code["code"], "name": "Pixel 8", "kind": "phone"}
        )
        dev = r.json()
        assert dev["token"] and dev["token"] != TOKEN and dev["capabilities"] == ["chat", "notify"]
        listing = (await c.get("/v1/devices", headers=AUTH)).json()
        assert [d["name"] for d in listing] == ["Pixel 8"] and "token" not in str(listing)
        # only a hash is stored
        from sqlalchemy import select

        from app.db.models import Device

        async with app.state.services.sessionmaker() as s:
            row = (await s.scalars(select(Device))).one()
        assert dev["token"] not in (row.token_hash, str(row.__dict__)) and len(row.token_hash) == 64


async def test_a_code_works_once_and_a_wrong_or_expired_code_does_not(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        code = (await c.post("/v1/devices/pairing-code", json={}, headers=AUTH)).json()["code"]
        body = {"code": code.lower().replace("-", " "), "name": "A", "kind": "phone"}
        assert (
            await c.post("/v1/devices/pair", json=body)
        ).status_code == 200  # case/dash tolerant
        assert (await c.post("/v1/devices/pair", json=body)).status_code == 400  # already used
        bad = await c.post("/v1/devices/pair", json={**body, "code": "ZZZZ-ZZZZ"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "pairing_failed"
        # an expired one
        code2 = (await c.post("/v1/devices/pairing-code", json={}, headers=AUTH)).json()["code"]
        reg = app.state.services.devices
        reg.clock = lambda: utcnow() + timedelta(minutes=6)
        late = await c.post("/v1/devices/pair", json={**body, "code": code2})
        assert late.status_code == 400


async def test_guessing_codes_is_rate_limited(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        codes = []
        for _ in range(8):
            r = await c.post(
                "/v1/devices/pair", json={"code": "AAAA-BBBB", "name": "x", "kind": "phone"}
            )
            codes.append(r.status_code)
    assert codes[:5] == [400] * 5 and 429 in codes[5:]


async def test_only_the_owner_makes_codes_and_unknown_capabilities_are_refused(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        assert (await c.post("/v1/devices/pairing-code", json={})).status_code == 401
        r = await c.post("/v1/devices/pairing-code", json={"capabilities": ["root"]}, headers=AUTH)
        assert r.status_code == 422


# ============================================================ what a device may do
async def test_a_device_can_chat_but_nothing_else_of_the_owners(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        dev = bearer((await pair_device(c))["token"])
        chat = await c.post("/v1/chat", json={"message": "hi"}, headers=dev)
        assert chat.status_code == 200
        for method, path in [
            ("get", "/v1/conversations"),
            ("get", "/v1/memories"),
            ("get", "/v1/devices"),
            ("get", "/v1/reminders"),
            ("post", "/v1/devices/pairing-code"),
            ("post", "/v1/notify"),
            ("get", "/v1/usage"),
            ("delete", "/v1/memories?confirm=delete-all-memories"),
        ]:
            r = await getattr(c, method)(path, headers=dev)
            assert r.status_code == 403, (path, r.status_code)


async def test_a_device_without_the_chat_capability_cannot_chat(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        dev = bearer((await pair_device(c, ["notify"]))["token"])
        assert (await c.post("/v1/chat", json={"message": "hi"}, headers=dev)).status_code == 403


async def test_a_wrong_token_is_unauthorised_and_a_revoked_device_stops_at_once(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        paired = await pair_device(c)
        dev = bearer(paired["token"])
        assert (await c.post("/v1/chat", json={"message": "hi"}, headers=dev)).status_code == 200
        assert (
            await c.post("/v1/chat", json={"message": "hi"}, headers=bearer("nope"))
        ).status_code == 401
        assert (
            await c.delete(f"/v1/devices/{paired['device_id']}", headers=AUTH)
        ).status_code == 204
        assert (await c.post("/v1/chat", json={"message": "hi"}, headers=dev)).status_code == 401
        assert (await c.delete("/v1/devices/" + "0" * 36, headers=AUTH)).status_code == 404
        assert (await c.get("/v1/devices", headers=AUTH)).json()[0]["revoked"] is True


async def test_at_most_ten_devices_can_be_paired(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        for i in range(10):
            await pair_device(c, name=f"d{i}")
            app.state.services.pair_limiter._hits.clear()  # the cap is under test, not the rate
        code = (await c.post("/v1/devices/pairing-code", json={}, headers=AUTH)).json()["code"]
        r = await c.post("/v1/devices/pair", json={"code": code, "name": "11th", "kind": "phone"})
        assert r.status_code == 400 and "At most 10" in r.json()["error"]["message"]


# ============================================================ push + outbox
@contextmanager
def server(tmp_path, **settings):
    app = create_app(make_settings(tmp_path, **settings), local=StreamingFake("local"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def pair_sync(client, caps=None):
    body = {"capabilities": caps} if caps else {}
    code = client.post("/v1/devices/pairing-code", json=body, headers=AUTH).json()["code"]
    r = client.post("/v1/devices/pair", json={"code": code, "name": "Pixel", "kind": "phone"})
    return r.json()


def test_a_notification_reaches_a_connected_device_and_is_cleared_by_its_ack(tmp_path):
    with server(tmp_path) as c:
        dev = pair_sync(c)
        with c.websocket_connect(PUSH, headers=bearer(dev["token"])) as ws:
            r = c.post(
                "/v1/notify", json={"title": "Build done", "body": "all green"}, headers=AUTH
            )
            assert r.json() == {"devices": 1}
            msg = ws.receive_json()
            assert msg["type"] == "push" and msg["kind"] == "notify"
            assert msg["payload"] == {"title": "Build done", "body": "all green"}
            ws.send_json({"type": "ack", "id": msg["id"]})
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
        with c.websocket_connect(PUSH, headers=bearer(dev["token"])) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}  # nothing was re-sent: it was acknowledged


def test_what_arrives_while_the_phone_is_offline_is_delivered_when_it_returns(tmp_path):
    with server(tmp_path) as c:
        dev = pair_sync(c)
        c.post("/v1/notify", json={"title": "one"}, headers=AUTH)
        c.post("/v1/notify", json={"title": "two"}, headers=AUTH)
        with c.websocket_connect(PUSH, headers=bearer(dev["token"])) as ws:
            titles = [ws.receive_json()["payload"]["title"] for _ in range(2)]
        assert titles == ["one", "two"]
        with c.websocket_connect(PUSH, headers=bearer(dev["token"])) as ws:  # never acked
            assert ws.receive_json()["payload"]["title"] == "one"  # sent again


def test_a_device_cannot_acknowledge_another_devices_items(tmp_path):
    with server(tmp_path) as c:
        a, b = pair_sync(c), pair_sync(c)
        c.post("/v1/notify", json={"title": "for both"}, headers=AUTH)
        with c.websocket_connect(PUSH, headers=bearer(a["token"])) as wa:
            item = wa.receive_json()
        with c.websocket_connect(PUSH, headers=bearer(b["token"])) as wb:
            wb.receive_json()
            wb.send_json({"type": "ack", "id": item["id"]})  # A's item, B's socket
            wb.send_json({"type": "ping"})
            assert wb.receive_json() == {"type": "pong"}  # the ack above has been processed
        with c.websocket_connect(PUSH, headers=bearer(a["token"])) as wa:
            wa.send_json({"type": "ping"})  # so a missing item shows up as a pong, not a hang
            first = wa.receive_json()
            assert first["type"] == "push" and first["payload"]["title"] == "for both"


def test_the_push_socket_refuses_strangers_browsers_and_revoked_or_unauthorised_devices(tmp_path):
    with server(tmp_path) as c:
        dev = pair_sync(c)
        chat_only = pair_sync(c, ["chat"])
        host = {"Host": "127.0.0.1:8000"}
        for headers in (
            {},  # no credentials
            bearer("nope"),
            AUTH,  # the owner token is not a device
            bearer(chat_only["token"]),  # no `notify`
            {**bearer(dev["token"]), **host, "Origin": "https://evil.example"},  # a web page
        ):
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(PUSH, headers=headers):
                    pass
        c.delete(f"/v1/devices/{dev['device_id']}", headers=AUTH)
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(PUSH, headers=bearer(dev["token"])):
                pass


def test_a_scheduled_reminder_is_pushed_to_the_phone(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "LOOP_SECONDS", 0.05)
    with server(tmp_path, scheduler_enabled=True) as c:
        dev = pair_sync(c)
        due = (utcnow() + timedelta(seconds=0.3)).isoformat()
        r = c.post("/v1/reminders", json={"text": "Stand up", "due_at": due}, headers=AUTH)
        assert r.status_code == 201
        with c.websocket_connect(PUSH, headers=bearer(dev["token"])) as ws:
            deadline = time.time() + 5
            msg = None
            while time.time() < deadline and msg is None:
                m = ws.receive_json()
                msg = m if m.get("kind") == "reminder" else None
            assert msg and msg["payload"]["text"] == "Stand up"
        assert c.get("/v1/reminders", headers=AUTH).json() == []  # no longer pending


# ============================================================ reminders
async def test_reminder_validation_and_cancel(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        soon = (utcnow() + timedelta(hours=1)).isoformat()
        ok = await c.post(
            "/v1/reminders", json={"text": "  Call   the dentist ", "due_at": soon}, headers=AUTH
        )
        assert ok.status_code == 201 and ok.json()["text"] == "Call the dentist"
        for body in (
            {"text": "x", "due_at": (utcnow() - timedelta(hours=1)).isoformat()},  # passed
            {"text": "x", "due_at": "2026-12-01T10:00:00"},  # no timezone
            {"text": "x", "due_at": (utcnow() + timedelta(days=500)).isoformat()},  # too far
            {"text": " ", "due_at": soon},
        ):
            assert (await c.post("/v1/reminders", json=body, headers=AUTH)).status_code == 422, body
        rid = ok.json()["id"]
        assert [r["id"] for r in (await c.get("/v1/reminders", headers=AUTH)).json()] == [rid]
        assert (await c.delete(f"/v1/reminders/{rid}", headers=AUTH)).status_code == 204
        assert (await c.get("/v1/reminders", headers=AUTH)).json() == []
        assert (await c.delete("/v1/reminders/" + "0" * 36, headers=AUTH)).status_code == 404


async def test_a_due_reminder_waits_for_a_device_and_gives_up_after_a_day(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        services = app.state.services
        r = await reminders.create(services, "water the plants", utcnow() + timedelta(seconds=1))
        later = utcnow() + timedelta(seconds=5)
        assert await reminders.fire_due(services, later) == 0  # nobody paired: stays pending
        assert [x.id for x in await reminders.listing(services)] == [r.id]
        dev = await pair_device(c)
        assert await reminders.fire_due(services, later) == 1  # delivered now
        assert await reminders.fire_due(services, later) == 0  # and not again
        pending = await services.hub.pending(dev["device_id"])
        assert [p["payload"]["text"] for p in pending] == ["water the plants"]

        await c.delete(f"/v1/devices/{dev['device_id']}", headers=AUTH)
        old = await reminders.create(services, "stale", utcnow() + timedelta(seconds=1))
        assert await reminders.fire_due(services, utcnow() + timedelta(hours=25)) == 1  # missed
        assert old.id not in [x.id for x in await reminders.listing(services)]


async def test_a_cancelled_reminder_is_never_delivered(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        services = app.state.services
        dev = await pair_device(c)
        r = await reminders.create(services, "nope", utcnow() + timedelta(seconds=1))
        await reminders.cancel(services, r.id)
        assert await reminders.fire_due(services, utcnow() + timedelta(minutes=5)) == 0
        assert await services.hub.pending(dev["device_id"]) == []


async def test_the_outbox_is_bounded_per_device(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        hub = app.state.services.hub
        dev = await pair_device(c)
        for i in range(210):
            await hub.enqueue(dev["device_id"], "notify", {"title": str(i)})
        pending = await hub.pending(dev["device_id"])
        assert len(pending) == 200 and pending[-1]["payload"]["title"] == "209"


def make_cert(tmp_path):
    import shutil
    import subprocess

    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=t",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )  # fmt: skip
    out = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return cert, out.strip().split("=", 1)[1]


async def test_the_pairing_code_carries_the_servers_certificate_fingerprint_when_tls_is_used(
    run_app, tmp_path
):
    import asyncio

    cert, expected = await asyncio.to_thread(make_cert, tmp_path)
    async with run_app(None, FakeAdapter("local"), tls_cert_file=cert) as (c, _):
        got = (await c.post("/v1/devices/pairing-code", json={}, headers=AUTH)).json()
    assert got["tls_fingerprint_sha256"] == expected
    async with run_app(None, FakeAdapter("local")) as (c, _):
        none = (await c.post("/v1/devices/pairing-code", json={}, headers=AUTH)).json()
    assert none["tls_fingerprint_sha256"] is None  # plain loopback: the phone must refuse this


def test_revoking_a_device_closes_its_open_connection_and_stops_pushes(tmp_path):
    with server(tmp_path) as c:
        dev = pair_sync(c)
        with c.websocket_connect(PUSH, headers=bearer(dev["token"])) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
            assert c.delete(f"/v1/devices/{dev['device_id']}", headers=AUTH).status_code == 204
            with pytest.raises(WebSocketDisconnect) as info:
                ws.receive_json()
            assert info.value.code == 1008
        assert c.post("/v1/notify", json={"title": "x"}, headers=AUTH).json() == {"devices": 0}


async def test_a_device_with_the_reminders_power_manages_reminders_and_nothing_else_new(run_app):
    from datetime import timedelta

    async with run_app(None, FakeAdapter("local")) as (c, _):
        dev = bearer((await pair_device(c, ["chat", "notify", "reminders"]))["token"])
        soon = (utcnow() + timedelta(hours=1)).isoformat()
        made = await c.post(
            "/v1/reminders", json={"text": "From the phone", "due_at": soon}, headers=dev
        )
        assert made.status_code == 201
        listed = (await c.get("/v1/reminders", headers=dev)).json()
        assert [r["text"] for r in listed] == ["From the phone"]
        assert (
            await c.delete(f"/v1/reminders/{made.json()['id']}", headers=dev)
        ).status_code == 204
        for method, path in [
            ("get", "/v1/devices"),
            ("get", "/v1/memories"),
            ("get", "/v1/conversations"),
            ("post", "/v1/notify"),
        ]:
            assert (await getattr(c, method)(path, headers=dev)).status_code == 403, path
        plain = bearer((await pair_device(c, ["chat", "notify"]))["token"])
        assert (await c.get("/v1/reminders", headers=plain)).status_code == 403  # no such power
