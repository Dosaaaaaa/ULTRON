from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from ultron_agent.broker import Agent  # noqa: E402
from ultron_agent.config import AgentConfig  # noqa: E402
from ultron_agent.server import AgentServer  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402
from websockets.datastructures import Headers  # noqa: E402
from websockets.http11 import Response  # noqa: E402

from ultron_desktop import fonts, theme  # noqa: E402
from ultron_desktop.settings import Settings  # noqa: E402

TOKEN = "test-token-DO-NOT-SHOW-7f3a"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    fonts.load()
    fonts.apply_default(app)
    app.setStyleSheet(theme.QSS)
    return app


def wait_until(cond: Callable[[], bool], timeout: float = 6.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    while time.monotonic() < deadline:
        app.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="ud"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- real device agent
class AgentThread:
    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.stopped = False
        self.loop = asyncio.new_event_loop()
        self.agent = Agent(cfg)
        self.server = AgentServer(self.agent, cfg.runtime_dir)
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.start())
        self._ready.set()
        self.loop.run_forever()

    def start(self) -> None:
        self.thread.start()
        assert self._ready.wait(5)

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def agent_env(short_tmp: Path) -> Iterator[dict[str, Any]]:
    ws, ro = short_tmp / "ws", short_tmp / "ro"
    ws.mkdir()
    ro.mkdir()
    (ro / "notes.txt").write_text("quarterly notes")
    (ro / "report.md").write_text("# report")
    cfg = AgentConfig(short_tmp / "conf", short_tmp / "state", short_tmp / "run")
    cfg.trash_dir = short_tmp / "trash"
    cfg.add_root(str(ws), write=True)
    cfg.add_root(str(ro), write=False)
    cfg.add_app("fake", "Fake App", ["/bin/true"])
    t = AgentThread(cfg)
    t.start()
    yield {"cfg": cfg, "ws": ws, "ro": ro, "thread": t, "runtime": cfg.runtime_dir}
    t.stop()


# --------------------------------------------------------------------------- fake backend
class FakeBackend:
    """Speaks the real chat WS protocol and a few REST routes; records what it received."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.auth_headers: list[str | None] = []
        self.paths: list[str] = []
        self.mode = "normal"  # normal | slow | error | locked | fallback
        self.reply = ["Seventeen ", "times twenty-three ", "is 391."]
        self.conversations = [
            {"id": "c1", "title": "Earlier chat", "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z", "message_count": 2, "local_only": False},
            {"id": "c2", "title": "Secret chat", "created_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z", "message_count": 2, "local_only": True},
        ]  # fmt: skip
        self.health_status = 200
        self.budget: dict | None = {
            "tokens_used_24h": 86000,
            "tokens_limit_24h": 200000,
            "tokens_used_1m": 1200,
            "tokens_limit_1m": 8000,
            "requests_24h": 70,
            "requests_limit_24h": 1000,
            "cooldown_s": 0,
        }
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    # ---- http
    def _http(self, connection, request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            self.auth_headers.append(request.headers.get("Authorization"))
            return None
        self.auth_headers.append(request.headers.get("Authorization"))
        self.paths.append(request.path)
        if self.health_status != 200:
            return Response(self.health_status, "x", Headers([("Content-Length", "2")]), b"{}")
        path = request.path.split("?")[0]
        if path == "/v1/health/ready":
            body = {"status": "ok", "engines": [
                {"provider": "ollama", "model": "qwen3:4b-instruct", "reachable": True},
                {"provider": "groq", "model": "qwen/qwen3.8-27b", "reachable": None}],
                "cloud_configured": True, "cloud_allowed": True, "cloud_budget": self.budget}  # fmt: skip
        elif path == "/v1/conversations":
            body = self.conversations
        elif path.startswith("/v1/conversations/"):
            cid = path.rsplit("/", 1)[1]
            body = {"id": cid, "title": "t", "local_only": cid == "c2", "messages": [
                {"id": 1, "role": "user", "content": "earlier question"},
                {"id": 2, "role": "assistant", "content": "earlier answer"}]}  # fmt: skip
        else:
            return Response(404, "Not Found", Headers([("Content-Length", "2")]), b"{}")
        raw = json.dumps(body).encode()
        return Response(
            200, "OK",
            Headers([("Content-Type", "application/json"), ("Content-Length", str(len(raw)))]),
            raw,
        )  # fmt: skip

    # ---- websocket
    async def _ws(self, ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            self.frames.append(msg)
            if msg["type"] == "cancel":
                continue
            if self.mode == "error":
                await ws.send(json.dumps({"type": "error", "code": "models_unavailable",
                                          "message": "No model could answer."}))  # fmt: skip
                continue
            locked = self.mode == "locked" or msg.get("privacy") == "local_only"
            await ws.send(json.dumps({
                "type": "start", "engine": {"provider": "groq", "model": "qwen/qwen3.8-27b",
                                            "tier": "cloud"},
                "fallback": False, "degraded": False,
                "notice": "Cloud model unavailable; answered by the local model." if self.mode == "fallback" else None,
                "privacy_locked": locked}))  # fmt: skip
            for piece in self.reply:
                await ws.send(json.dumps({"type": "delta", "text": piece}))
                if self.mode == "slow":
                    await asyncio.sleep(0.4)
            await ws.send(json.dumps({
                "type": "end", "conversation_id": msg.get("conversation_id") or "new-conv-1",
                "message_id": 7, "reply": "".join(self.reply), "interrupted": False,
                "privacy_locked": locked, "notice": None}))  # fmt: skip

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def main() -> None:
            async with serve(self._ws, "127.0.0.1", 0, process_request=self._http) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await asyncio.Future()

        try:
            self._loop.run_until_complete(main())
        except asyncio.CancelledError:
            pass

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(5)

    def stop(self) -> None:
        for t in asyncio.all_tasks(self._loop):
            self._loop.call_soon_threadsafe(t.cancel)
        self._thread.join(3)


@pytest.fixture
def backend() -> Iterator[FakeBackend]:
    b = FakeBackend()
    b.start()
    yield b
    b.stop()


@pytest.fixture
def settings(backend: FakeBackend, short_tmp: Path) -> Settings:
    s = Settings()
    s.backend_url = f"http://127.0.0.1:{backend.port}"
    s.token = TOKEN
    s.agent_runtime_dir = short_tmp / "run"
    s.voice_state_file = short_tmp / "voice.state"
    return s
