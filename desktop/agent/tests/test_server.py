from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from ultron_agent.client import AgentCallError, AgentClient
from ultron_agent.server import AgentServer


async def test_sockets_are_private(server, cfg):
    assert stat.S_IMODE(cfg.runtime_dir.stat().st_mode) == 0o700
    for p in (server.request_path, server.console_path):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


async def test_request_socket_can_call_but_cannot_approve_or_read_audit(server, agent, ws):
    async with AgentClient(server.request_path) as c:
        assert (await c.simple("status"))["tools"] >= 10
        for op, extra in (
            ("decide", {"approval_id": "x", "approve": True}),
            ("pending", {}),
            ("audit_tail", {}),
            ("audit_verify", {}),
        ):
            with pytest.raises(AgentCallError) as e:
                await c.simple(op, **extra)
            assert e.value.code == "forbidden", op
        with pytest.raises(AgentCallError) as e:
            await c.simple("rm_rf")
        assert e.value.code == "unknown_op"


async def test_requester_cannot_approve_its_own_request(server, agent, ws):
    """The security property that matters for Phase 6: the asker is never the approver."""
    async with AgentClient(server.request_path) as req:
        call = asyncio.create_task(
            req.call("files.create", {"path": str(ws / "n.txt"), "content": "x"})
        )
        await asyncio.sleep(0.2)
        assert len(agent.pending) == 1
        approval_id = next(iter(agent.pending))
        with pytest.raises(AgentCallError) as e:  # try to self-approve through the same socket
            await req.simple("decide", approval_id=approval_id, approve=True)
        assert e.value.code == "forbidden"
        assert not (ws / "n.txt").exists() and not call.done()
        async with AgentClient(server.console_path) as console:  # the human
            await console.simple("decide", approval_id=approval_id, approve=True)
        assert (await call)["bytes"] == 1 and (ws / "n.txt").exists()


async def test_console_sees_events_and_decides(server, agent, ws):
    async with AgentClient(server.console_path) as console, AgentClient(server.request_path) as req:
        call = asyncio.create_task(
            req.call("files.create", {"path": str(ws / "e.txt"), "content": "e"})
        )
        while True:
            ev = await asyncio.wait_for(console.events.get(), 3)
            if ev["event"] == "approval_requested":
                break
        ap = ev["approval"]
        assert (
            ap["tool"] == "files.create"
            and ap["level"] == "P2"
            and ap["confirmation_required"] is None
        )
        assert [a["id"] for a in await console.simple("pending")] == [ap["id"]]
        await console.simple("decide", approval_id=ap["id"], approve=False)
        with pytest.raises(AgentCallError) as e:
            await call
        assert e.value.code == "denied" and not (ws / "e.txt").exists()
        recs = await console.simple("audit_tail", n=5)
        assert recs[-1]["decision"] == "refused_by_operator"
        assert (await console.simple("audit_verify"))["ok"]


async def test_requester_disconnect_cancels_pending_approval(server, agent, ws):
    async with AgentClient(server.console_path) as console:
        req = AgentClient(server.request_path)
        await req.__aenter__()
        asyncio.create_task(req.call("files.create", {"path": str(ws / "z.txt"), "content": "z"}))
        while (await asyncio.wait_for(console.events.get(), 3))["event"] != "approval_requested":
            pass
        assert len(agent.pending) == 1
        await req.__aexit__(None, None, None)
        for _ in range(40):
            if not agent.pending:
                break
            await asyncio.sleep(0.05)
        assert not agent.pending and not (ws / "z.txt").exists()


async def test_garbage_input_does_not_break_the_server(server):
    r, w = await asyncio.open_unix_connection(str(server.request_path))
    for junk in (
        b"not json\n",
        b"[1,2]\n",
        b'{"op": 5}\n',
        b'{"id":1,"op":"call","tool":{"a":1}}\n',
    ):
        w.write(junk)
    await w.drain()
    lines = [json.loads(await asyncio.wait_for(r.readline(), 2)) for _ in range(4)]
    assert all(not m["ok"] for m in lines)
    w.close()
    async with AgentClient(server.request_path) as c:  # still alive
        assert (await c.simple("status"))["tools"]


async def _closed_without_answer(r: asyncio.StreamReader) -> bool:
    try:
        return await asyncio.wait_for(r.read(), 3) == b""
    except ConnectionResetError:
        return True


async def test_oversize_message_is_dropped(server):
    r, w = await asyncio.open_unix_connection(str(server.request_path))
    w.write(b"x" * (5 * 1024 * 1024) + b"\n")
    try:
        await w.drain()
    except ConnectionError:
        pass
    assert await _closed_without_answer(r)
    async with AgentClient(server.request_path) as c:  # the server itself is unharmed
        assert (await c.simple("status"))["tools"]


async def test_second_agent_refuses_to_start_and_stale_socket_is_reclaimed(agent, cfg, server):
    other = AgentServer(agent, cfg.runtime_dir)
    with pytest.raises(RuntimeError, match="already running"):
        await other.start()
    await server.stop()
    server.request_path.write_text("")  # leftover non-socket file from a crash
    s2 = AgentServer(agent, cfg.runtime_dir)
    await s2.start()
    async with AgentClient(s2.request_path) as c:
        assert (await c.simple("status"))["tools"]
    await s2.stop()


async def test_foreign_uid_is_rejected(server, monkeypatch):
    monkeypatch.setattr("ultron_agent.server.peer_uid", lambda s: os.getuid() + 1)
    r, w = await asyncio.open_unix_connection(str(server.request_path))
    try:
        w.write(b'{"id":1,"op":"status"}\n')
        await w.drain()
        assert await _closed_without_answer(r)  # closed without an answer
    finally:
        w.close()


async def test_stop_does_not_hang_on_connected_clients_and_cancels_pending(server, agent, ws):
    req = AgentClient(server.request_path)
    ui = AgentClient(server.console_path)
    await req.__aenter__()
    await ui.__aenter__()
    call = asyncio.create_task(
        req.call("files.create", {"path": str(ws / "p.txt"), "content": "x"})
    )
    await asyncio.sleep(0.2)
    assert len(agent.pending) == 1
    await asyncio.wait_for(server.stop(), 5)  # clients are still connected
    for _ in range(40):
        if not agent.pending:
            break
        await asyncio.sleep(0.05)
    assert not agent.pending and not (ws / "p.txt").exists()
    with pytest.raises((ConnectionError, AgentCallError)):
        await asyncio.wait_for(call, 3)
    await req.__aexit__(None, None, None)
    await ui.__aexit__(None, None, None)
