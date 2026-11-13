"""Cloud usage budget: stay under the provider's quota instead of discovering it through 429s.

Why this exists: Groq's free tier allows ~200,000 tokens per rolling day, 8,000 per minute and
1,000 requests per day, and those limits belong to the *organization*: extra API keys from the same
account share one bucket, so rotating keys adds nothing. What does help is spending the single
bucket deliberately. `CloudBudget` keeps a rolling ledger, refuses a request *before* it is sent
when it would not fit (the router then answers locally, with no wasted round trip), and
corrects its own estimate from the provider's 429 body, because other programs may be spending
the same quota.

`BudgetedAdapter` wraps any cloud adapter. The router sees an ordinary adapter whose failure kind is
`budget`, so fallback, privacy rules and notices need no special cases.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import tempfile
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

from app.models.base import (
    AdapterHealth,
    ModelAdapter,
    ModelError,
    ModelRequest,
    ModelResponse,
    ProviderUnavailable,
    StreamChunk,
)

log = logging.getLogger("ultron.budget")

DAY_S = 86_400.0
MINUTE_S = 60.0
CHARS_PER_TOKEN = 3.2  # pessimistic for English; replaced by real usage after the call
MAX_WAIT_S = 4.0  # a per-minute shortfall this short is waited out, not sent to the local model


class BudgetExhausted(ProviderUnavailable):
    """Raised before any network call: the request would not fit in the quota."""

    kind = "budget"

    def __init__(self, which: str, retry_after_s: float) -> None:
        super().__init__(f"cloud {which} budget exhausted; room in {math.ceil(retry_after_s)}s")
        self.which = which
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class Limits:
    """0 disables a dimension (unknown limits are left to the provider's own 429)."""

    tokens_per_day: int = 0
    tokens_per_minute: int = 0
    requests_per_day: int = 0
    requests_per_minute: int = 0
    safety: float = 0.9  # use at most this fraction: estimates are estimates

    @property
    def enabled(self) -> bool:
        return any((self.tokens_per_day, self.tokens_per_minute, self.requests_per_day,
                    self.requests_per_minute))  # fmt: skip


@dataclass
class Reservation:
    tokens: int  # held against the budget until commit/release
    prompt_estimate: int
    done: bool = False


def estimate_tokens(request: ModelRequest) -> int:
    chars = sum(len(m.content) for m in request.messages)
    return math.ceil(chars / CHARS_PER_TOKEN) + 4 * len(request.messages)


_LIMIT_RE = re.compile(
    r"\((TPD|TPM|RPD|RPM)\):\s*Limit\s+(\d+),\s*Used\s+(\d+)", re.IGNORECASE
)  # Groq's 429 body: "... tokens per day (TPD): Limit 200000, Used 199497, Requested 1200 ..."


class CloudBudget:
    def __init__(
        self,
        limits: Limits,
        *,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.limits = limits
        self._path = path
        self._clock = clock
        self._tokens: deque[tuple[float, int]] = deque()  # (timestamp, tokens), last 24 h
        self._requests: deque[float] = deque()
        self._held_tokens = 0  # reservations in flight
        self._held_requests = 0
        self._cooldown_until = 0.0
        self.last_429: float | None = None
        self._load()

    # ------------------------------------------------------------------ accounting
    def _prune(self, now: float) -> None:
        while self._tokens and self._tokens[0][0] <= now - DAY_S:
            self._tokens.popleft()
        while self._requests and self._requests[0] <= now - DAY_S:
            self._requests.popleft()

    def _used(self, now: float, window: float) -> int:
        return sum(t for ts, t in self._tokens if ts > now - window)

    def _count(self, now: float, window: float) -> int:
        return sum(1 for ts in self._requests if ts > now - window)

    @staticmethod
    def _wait_for_room(entries: list[tuple[float, int]], need_free: int, window: float,
                       now: float) -> float:  # fmt: skip
        """Seconds until `need_free` tokens have aged out of `window` (entries are oldest first)."""
        freed = 0
        for ts, tokens in entries:
            freed += tokens
            if freed >= need_free:
                return max(ts + window - now, 0.0) + 0.05
        return window  # does not fit even when empty (request larger than the whole limit)

    def reserve(self, tokens: int) -> Reservation:
        """Admit a request or raise BudgetExhausted. No awaits, so callers cannot interleave."""
        now = self._clock()
        self._prune(now)
        lim, safety = self.limits, self.limits.safety
        if now < self._cooldown_until:
            raise BudgetExhausted("provider cooldown", self._cooldown_until - now)

        checks = (
            ("daily token", lim.tokens_per_day, DAY_S, tokens),
            ("per-minute token", lim.tokens_per_minute, MINUTE_S, tokens),
        )
        for name, limit, window, need in checks:
            if not limit:
                continue
            cap = int(limit * safety)
            used = self._used(now, window) + self._held_tokens
            if used + need > cap:
                if need > cap:
                    raise BudgetExhausted(name, window)  # can never fit
                entries = [(ts, t) for ts, t in self._tokens if ts > now - window]
                raise BudgetExhausted(
                    name, self._wait_for_room(entries, used + need - cap, window, now)
                )
        for name, limit, window in (
            ("daily request", lim.requests_per_day, DAY_S),
            ("per-minute request", lim.requests_per_minute, MINUTE_S),
        ):
            if not limit:
                continue
            cap = max(int(limit * safety), 1)
            used = self._count(now, window) + self._held_requests
            if used + 1 > cap:
                stamps = [ts for ts in self._requests if ts > now - window]
                idx = used + 1 - cap - 1
                wait = (stamps[idx] + window - now + 0.05) if idx < len(stamps) else window
                raise BudgetExhausted(name, max(wait, 0.0))

        self._held_tokens += tokens
        self._held_requests += 1
        return Reservation(tokens, tokens)

    def commit(self, res: Reservation, actual_tokens: int | None) -> None:
        """Replace the reservation by what was really spent (or by itself if usage is unknown)."""
        if res.done:
            return
        res.done = True
        self._held_tokens -= res.tokens
        self._held_requests -= 1
        now = self._clock()
        spent = res.tokens if actual_tokens is None else max(actual_tokens, 0)
        self._tokens.append((now, spent))
        self._requests.append(now)
        self._save()

    def release(self, res: Reservation) -> None:
        """The request spent nothing (never sent, or rejected): hand the reservation back."""
        if res.done:
            return
        res.done = True
        self._held_tokens -= res.tokens
        self._held_requests -= 1

    def observe_rate_limit(self, body: str, retry_after_s: float | None) -> None:
        """The provider said 429. Trust it over our own estimate."""
        now = self._clock()
        self.last_429 = now
        windows = {"TPD": DAY_S, "TPM": MINUTE_S}
        for kind, limit, used in _LIMIT_RE.findall(body or ""):
            window = windows.get(kind.upper())
            if window is None:
                continue
            ours = self._used(now, window)
            if int(used) > ours:  # someone else spends this quota too: record it
                self._tokens.append((now, int(used) - ours))
            log.warning(
                "cloud_quota_reported",
                extra={"window": kind.upper(), "limit": int(limit), "used": int(used)},
            )
        if retry_after_s and retry_after_s > 0:
            self._cooldown_until = max(self._cooldown_until, now + min(retry_after_s, DAY_S))
        else:
            self._cooldown_until = max(self._cooldown_until, now + 5.0)
        self._save()

    # ------------------------------------------------------------------ reporting
    def snapshot(self) -> dict:
        now = self._clock()
        self._prune(now)
        lim = self.limits
        out: dict = {
            "tokens_used_24h": self._used(now, DAY_S),
            "tokens_limit_24h": lim.tokens_per_day or None,
            "tokens_used_1m": self._used(now, MINUTE_S),
            "tokens_limit_1m": lim.tokens_per_minute or None,
            "requests_24h": self._count(now, DAY_S),
            "requests_limit_24h": lim.requests_per_day or None,
            "cooldown_s": max(round(self._cooldown_until - now), 0),
            "safety": lim.safety,
        }
        if lim.tokens_per_day:
            out["day_fraction"] = round(out["tokens_used_24h"] / lim.tokens_per_day, 3)
        return out

    # ------------------------------------------------------------------ persistence
    def _save(self) -> None:
        if not self._path:
            return
        data = {
            "v": 1,
            "tokens": [[round(ts, 2), t] for ts, t in self._tokens],
            "requests": [round(ts, 2) for ts in self._requests],
            "cooldown_until": round(self._cooldown_until, 2),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".budget-")
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump(data, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError:
            log.warning("cloud_budget_not_saved")  # losing the ledger only makes us less careful

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        now = self._clock()
        try:
            data = json.loads(self._path.read_text())
            tokens = [(float(ts), int(t)) for ts, t in data["tokens"]]
            requests = [float(ts) for ts in data["requests"]]
            cooldown = float(data.get("cooldown_until", 0))
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("cloud_budget_unreadable_starting_empty")
            return
        # a clock that moved backwards must not make old entries live forever
        self._tokens = deque(sorted((ts, t) for ts, t in tokens if now - DAY_S < ts <= now + 60))
        self._requests = deque(sorted(ts for ts in requests if now - DAY_S < ts <= now + 60))
        self._cooldown_until = cooldown if cooldown <= now + DAY_S else 0.0


class BudgetedAdapter(ModelAdapter):
    """Wraps a cloud adapter; same interface, so the router needs no budget knowledge."""

    tier = "cloud"

    def __init__(
        self, inner: ModelAdapter, budget: CloudBudget, *, max_wait_s: float = MAX_WAIT_S
    ) -> None:
        self._inner = inner
        self.budget = budget
        self._max_wait_s = max_wait_s
        self.provider = inner.provider
        self.model = inner.model
        self.timeout_s = inner.timeout_s

    async def _admit(self, request: ModelRequest) -> Reservation:
        prompt = estimate_tokens(request)
        reserve = prompt + request.max_output_tokens
        try:
            return self.budget.reserve(reserve)
        except BudgetExhausted as exc:
            if exc.retry_after_s > self._max_wait_s:
                raise
            wait = exc.retry_after_s
        await asyncio.sleep(wait)  # short per-minute shortfall: wait, then try once
        return self.budget.reserve(reserve)

    def _on_error(self, res: Reservation, exc: ModelError) -> None:
        self.budget.release(res)
        body = getattr(exc, "body", None)
        if body is not None or getattr(exc, "retry_after_s", None) is not None:
            self.budget.observe_rate_limit(body or "", getattr(exc, "retry_after_s", None))

    async def complete(self, request: ModelRequest) -> ModelResponse:
        res = await self._admit(request)
        try:
            response = await self._inner.complete(request)
        except ModelError as exc:
            self._on_error(res, exc)
            raise
        except BaseException:
            self.budget.commit(res, None)  # cancelled mid-flight: the provider may have spent it
            raise
        self.budget.commit(res, _spent(response))
        return response

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk | ModelResponse]:
        res = await self._admit(request)
        inner = self._inner.stream(request)
        produced = 0
        try:
            async for item in inner:
                if isinstance(item, StreamChunk):
                    produced += len(item.text)
                else:
                    self.budget.commit(res, _spent(item))
                yield item
        except ModelError as exc:
            if produced:
                self.budget.commit(res, res.prompt_estimate + math.ceil(produced / CHARS_PER_TOKEN))
            else:
                self._on_error(res, exc)
            raise
        finally:
            # closed early (client left) or cancelled: the provider already generated some of it
            self.budget.commit(res, res.prompt_estimate + math.ceil(produced / CHARS_PER_TOKEN))
            await inner.aclose()

    async def health(self) -> AdapterHealth:
        h = await self._inner.health()
        snap = self.budget.snapshot()
        if snap.get("tokens_limit_24h"):
            h = AdapterHealth(
                h.provider, h.model, h.tier, h.configured, h.reachable,
                f"{h.detail}; {snap['tokens_used_24h']}/{snap['tokens_limit_24h']} tokens in 24h",
            )  # fmt: skip
        return h


def _spent(r: ModelResponse) -> int | None:
    if r.prompt_tokens is None and r.completion_tokens is None:
        return None
    return (r.prompt_tokens or 0) + (r.completion_tokens or 0)
