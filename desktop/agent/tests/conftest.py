from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ultron_agent.broker import Agent
from ultron_agent.config import AgentConfig
from ultron_agent.server import AgentServer


@pytest.fixture
def short_tmp() -> Path:
    """AF_UNIX paths are limited to ~107 bytes, pytest's tmp_path can be too long."""
    d = Path(tempfile.mkdtemp(prefix="ua"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def cfg(short_tmp: Path) -> AgentConfig:
    ws = short_tmp / "ws"
    ro = short_tmp / "ro"
    ws.mkdir()
    ro.mkdir()
    c = AgentConfig(short_tmp / "conf", short_tmp / "state", short_tmp / "run")
    c.trash_dir = short_tmp / "trash"
    c.add_root(str(ws), write=True)
    c.add_root(str(ro), write=False)
    c.stored.approval_timeout_s = 5
    return c


@pytest.fixture
def ws(cfg: AgentConfig) -> Path:
    return Path(cfg.stored.roots[0].path)


@pytest.fixture
def ro(cfg: AgentConfig) -> Path:
    return Path(cfg.stored.roots[1].path)


@pytest.fixture
def agent(cfg: AgentConfig) -> Agent:
    return Agent(cfg)


@pytest.fixture
async def server(agent: Agent, cfg: AgentConfig) -> AsyncIterator[AgentServer]:
    s = AgentServer(agent, cfg.runtime_dir)
    await s.start()
    yield s
    await s.stop()


def approve_next(agent: Agent, *, approve: bool = True, confirmation: str | None = None):
    """A background 'human' that decides the first approval request that appears."""
    import asyncio

    q = agent.subscribe()  # subscribe NOW, so no event can be missed before the task first runs

    async def human():
        try:
            while True:
                ev = await q.get()
                if ev["event"] == "approval_requested":
                    agent.decide(ev["approval"]["id"], approve, confirmation, "test-human")
                    return ev["approval"]
        finally:
            agent.unsubscribe(q)

    return asyncio.create_task(human())


assert os.name == "posix"
