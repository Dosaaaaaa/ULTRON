"""Process-wide dependencies, built once in the app lifespan."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.core.errors import RateLimited
from app.core.personality import PersonalityEngine
from app.devices.hub import PushHub
from app.devices.registry import DeviceRegistry
from app.memory.store import MemoryStore
from app.models.router import ModelRouter
from app.tools.web import DailyCap, SearchProvider


class SlidingWindowLimiter:
    """In-memory per-key limiter. Single-process only; revisit if the backend is scaled out."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self._limit = limit
        self._window = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            retry = max(1, int(self._window - (now - hits[0])) + 1)
            raise RateLimited("Rate limit exceeded.", extra={"retry_after": retry})
        hits.append(now)


@dataclass
class Services:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker
    http: httpx.AsyncClient
    router: ModelRouter
    personality: PersonalityEngine
    limiter: SlidingWindowLimiter
    memory: MemoryStore | None = None
    web: SearchProvider | None = None
    web_cap: DailyCap | None = None
    devices: DeviceRegistry | None = None
    hub: PushHub | None = None
    pair_limiter: SlidingWindowLimiter | None = None  # pairing codes are short: 5 tries a minute
    conversation_locks: dict[str, object] = field(default_factory=dict)
