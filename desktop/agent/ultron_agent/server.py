"""Transport: newline-delimited JSON over two Unix domain sockets. No TCP port is ever opened.

    request.sock  - may list tools and CALL them. Cannot approve, cannot read the audit log.
    console.sock  - the human's UI: everything above, plus decide / pending / audit / events.

Both live in a 0700 directory, are chmod 0600, and every connection's kernel-reported UID
(SO_PEERCRED) must equal the agent's UID. A future model relay gets request.sock only, so it can
ask for a risky action but cannot approve its own request.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import struct
from pathlib import Path
from typing import Any

from ultron_agent.broker import Agent, AgentError
from ultron_agent.config import private_dir

MAX_LINE = 4 * 1024 * 1024
REQUEST_OPS = {"tools", "status", "call"}
CONSOLE_OPS = REQUEST_OPS | {"pending", "decide", "audit_tail", "audit_verify"}


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


class AgentServer:
    def __init__(self, agent: Agent, runtime_dir: Path) -> None:
        self.agent = agent
        self.dir = runtime_dir
        self.request_path = runtime_dir / "request.sock"
        self.console_path = runtime_dir / "console.sock"
        self._servers: list[asyncio.AbstractServer] = []
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        private_dir(self.dir)
        for path, console in ((self.request_path, False), (self.console_path, True)):
            await self._claim(path)
            srv = await asyncio.start_unix_server(
                lambda r, w, c=console: self._conn(r, w, c), path=str(path), limit=MAX_LINE
            )
            path.chmod(0o600)
            self._servers.append(srv)

    async def _claim(self, path: Path) -> None:
        """Remove a stale socket, but refuse to start if another agent is alive on it."""
        if not path.exists():  # noqa: ASYNC240
            return
        try:
            _r, w = await asyncio.open_unix_connection(str(path))
        except OSError:
            path.unlink(missing_ok=True)  # noqa: ASYNC240
            return
        w.close()
        raise RuntimeError("another ultron-agent is already running")

    async def stop(self) -> None:
        for s in self._servers:
            s.close()
        for w in list(self._writers):  # connected clients must not be able to block shutdown
            w.close()
        for s in self._servers:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(s.wait_closed(), 3)
        for p in (self.request_path, self.console_path):
            p.unlink(missing_ok=True)

    # ---------------------------------------------------------------- connection handling
    async def _conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, console: bool
    ) -> None:
        sock = writer.get_extra_info("socket")
        try:
            if peer_uid(sock) != os.getuid():
                writer.close()
                return
        except OSError:
            writer.close()
            return
        source = "console" if console else "request"
        self._writers.add(writer)
        send_lock = asyncio.Lock()
        tasks: set[asyncio.Task] = set()

        async def send(obj: dict[str, Any]) -> None:
            async with send_lock:
                writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
                await writer.drain()

        events_task = None
        if console:
            q = self.agent.subscribe()

            async def pump() -> None:
                while True:
                    await send(await q.get())

            events_task = asyncio.create_task(pump())
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                    if not isinstance(msg, dict):
                        raise ValueError
                except ValueError:
                    await send({"id": None, "ok": False, "error": {"code": "bad_json"}})
                    continue
                t = asyncio.create_task(self._handle(msg, console, source, send))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
        except (asyncio.LimitOverrunError, ValueError, ConnectionError):
            pass
        finally:
            for t in tasks:  # a vanished requester cancels its pending calls and approvals
                t.cancel()
            if events_task:
                events_task.cancel()
                self.agent.unsubscribe(q)
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    async def _handle(self, msg: dict[str, Any], console: bool, source: str, send) -> None:
        rid, op = msg.get("id"), msg.get("op")
        allowed = CONSOLE_OPS if console else REQUEST_OPS
        try:
            if op not in allowed:
                raise AgentError("forbidden" if op in CONSOLE_OPS else "unknown_op", str(op)[:40])
            if op == "tools":
                data: Any = self.agent.registry.listing()
            elif op == "status":
                data = self.agent.status()
            elif op == "call":
                out = await self.agent.call(msg.get("tool"), msg.get("args"), source=source)
                await send({"id": rid, **out})
                return
            elif op == "pending":
                data = [a.public() for a in self.agent.pending.values()]
            elif op == "decide":
                self.agent.decide(
                    str(msg.get("approval_id")),
                    bool(msg.get("approve")),
                    msg.get("confirmation"),
                    "console",
                )
                data = {"decided": True}
            elif op == "audit_tail":
                n = msg.get("n", 50)
                data = self.agent.audit.tail(n if isinstance(n, int) else 50)
            else:  # audit_verify
                v = self.agent.audit.verify()
                data = {
                    "ok": v.ok,
                    "records": v.records,
                    "first_bad_line": v.first_bad_line,
                    "reason": v.reason,
                }
            await send({"id": rid, "ok": True, "result": data})
        except AgentError as e:
            await send({"id": rid, "ok": False, "error": {"code": e.code, "message": e.message}})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - never crash the server or leak internals
            await send(
                {"id": rid, "ok": False, "error": {"code": "internal", "message": type(e).__name__}}
            )
