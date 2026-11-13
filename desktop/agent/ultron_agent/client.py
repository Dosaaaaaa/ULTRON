"""Asyncio client for the agent protocol (tests and CLI; the Qt app uses QLocalSocket)."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from pathlib import Path
from typing import Any


class AgentCallError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


class AgentClient:
    def __init__(self, socket_path: Path) -> None:
        self.path = socket_path
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._ids = itertools.count(1)
        self._waiting: dict[int, asyncio.Future] = {}

    async def __aenter__(self) -> AgentClient:
        self._r, self._w = await asyncio.open_unix_connection(str(self.path), limit=4 * 1024 * 1024)
        self._reader_task = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._reader_task.cancel()
        self._w.close()
        with contextlib.suppress(OSError):
            await self._w.wait_closed()

    async def _read(self) -> None:
        while line := await self._r.readline():
            msg = json.loads(line)
            if "event" in msg:
                self.events.put_nowait(msg)
            elif (fut := self._waiting.pop(msg.get("id"), None)) and not fut.done():
                fut.set_result(msg)
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(ConnectionError("agent closed the connection"))

    async def request(self, op: str, **fields: Any) -> dict[str, Any]:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        self._w.write((json.dumps({"id": rid, "op": op, **fields}) + "\n").encode())
        await self._w.drain()
        return await fut

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        r = await self.request("call", tool=tool, args=args or {})
        if not r.get("ok"):
            e = r.get("error", {})
            raise AgentCallError(e.get("code", "error"), e.get("message", ""))
        return r["result"]

    async def simple(self, op: str, **fields: Any) -> Any:
        r = await self.request(op, **fields)
        if not r.get("ok"):
            e = r.get("error", {})
            raise AgentCallError(e.get("code", "error"), e.get("message", ""))
        return r["result"]
