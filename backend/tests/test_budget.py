"""Cloud budget: admission, rolling windows, 429 correction, persistence, router fallback."""

from __future__ import annotations

import asyncio
import json
import stat

import httpx
import pytest

from app.models.base import (
    ChatMessage,
    ModelRequest,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.models.budget import (
    DAY_S,
    BudgetedAdapter,
    BudgetExhausted,
    CloudBudget,
    Limits,
    estimate_tokens,
)
from app.models.openai_compat import OpenAICompatAdapter
from app.models.router import ModelRouter
from tests.fakes import FakeAdapter, StreamingFake

GROQ_429 = (
    '{"error":{"message":"Rate limit reached for model `m` in organization `org_x` service tier '
    "`on_demand` on tokens per day (TPD): Limit 200000, Used 199497, Requested 1200. "
    'Please try again in 8m4s.","type":"tokens","code":"rate_limit_exceeded"}}'
)


class SlowFake(FakeAdapter):
    async def complete(self, request):
        await asyncio.sleep(0.02)
        return await super().complete(request)


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(clock=None, **limits) -> tuple[CloudBudget, Clock]:
    clock = clock or Clock()
    limits.setdefault("safety", 1.0)
    return CloudBudget(Limits(**limits), clock=clock), clock


def req(text: str = "hi", max_out: int = 100) -> ModelRequest:
    return ModelRequest([ChatMessage("user", text)], max_out, 0.6)


# ------------------------------------------------------------------ admission
def test_admits_until_the_daily_cap_then_refuses_with_a_real_wait():
    b, clock = make(tokens_per_day=1000)
    r1 = b.reserve(600)
    b.commit(r1, 600)
    clock.t += 3600
    with pytest.raises(BudgetExhausted) as e:
        b.reserve(600)
    # the 600 spent an hour ago frees up 23 h after it was spent, not "someday"
    assert e.value.which == "daily token" and abs(e.value.retry_after_s - (DAY_S - 3600)) < 1


def test_budget_rolls_instead_of_resetting_at_midnight():
    b, clock = make(tokens_per_day=1000)
    b.commit(b.reserve(500), 500)
    clock.t += 43_200
    b.commit(b.reserve(500), 500)
    clock.t += 43_200 + 1  # the first 500 is now older than 24 h, the second is not
    assert b.snapshot()["tokens_used_24h"] == 500
    b.commit(b.reserve(500), 500)  # fits again


def test_safety_margin_is_applied():
    b, _ = make(tokens_per_day=1000, safety=0.9)
    b.reserve(900)
    with pytest.raises(BudgetExhausted):
        b.reserve(1)


def test_in_flight_reservations_count_so_concurrent_calls_cannot_overshoot():
    b, _ = make(tokens_per_day=1000)
    held = [b.reserve(400), b.reserve(400)]
    with pytest.raises(BudgetExhausted):
        b.reserve(400)
    b.release(held[0])  # a request that never left frees its share at once
    b.reserve(400)


def test_commit_replaces_the_reservation_with_actual_usage():
    b, _ = make(tokens_per_day=10_000)
    r = b.reserve(3000)
    b.commit(r, 1300)
    assert b.snapshot()["tokens_used_24h"] == 1300
    b.commit(r, 99)  # second commit is ignored, not double-counted
    b.release(r)
    assert b.snapshot()["tokens_used_24h"] == 1300


def test_unknown_usage_is_charged_at_the_reservation_not_zero():
    b, _ = make(tokens_per_day=10_000)
    b.commit(b.reserve(3000), None)
    assert b.snapshot()["tokens_used_24h"] == 3000


def test_per_minute_window_is_independent_of_the_daily_one():
    b, clock = make(tokens_per_day=100_000, tokens_per_minute=1000)
    b.commit(b.reserve(900), 900)
    with pytest.raises(BudgetExhausted) as e:
        b.reserve(200)
    assert e.value.which == "per-minute token" and e.value.retry_after_s <= 60.1
    clock.t += 61
    b.reserve(200)


def test_request_count_limits():
    b, _ = make(requests_per_day=2)
    b.commit(b.reserve(1), 1)
    b.commit(b.reserve(1), 1)
    with pytest.raises(BudgetExhausted) as e:
        b.reserve(1)
    assert e.value.which == "daily request"


def test_a_request_bigger_than_the_whole_limit_is_refused_not_queued():
    b, _ = make(tokens_per_minute=1000)
    with pytest.raises(BudgetExhausted) as e:
        b.reserve(5000)
    assert e.value.retry_after_s >= 60


def test_no_limits_configured_admits_everything():
    b, _ = make()
    assert not b.limits.enabled
    for _ in range(50):
        b.commit(b.reserve(10**6), 10**6)


# ------------------------------------------------------------------ provider correction
def test_a_429_body_corrects_our_ledger_upward():
    b, _ = make(tokens_per_day=200_000)
    b.commit(b.reserve(1000), 1000)  # we believe 1,000 were used
    b.observe_rate_limit(GROQ_429, retry_after_s=484)
    assert b.snapshot()["tokens_used_24h"] == 199_497  # the provider knows about other spenders
    with pytest.raises(BudgetExhausted) as e:
        b.reserve(10)
    assert e.value.which == "provider cooldown" and 480 <= e.value.retry_after_s <= 484


def test_a_429_never_lowers_the_ledger():
    b, _ = make(tokens_per_day=200_000)
    b.commit(b.reserve(5000), 5000)
    b.observe_rate_limit(GROQ_429.replace("Used 199497", "Used 10"), 1)
    assert b.snapshot()["tokens_used_24h"] == 5000


def test_a_429_without_retry_after_still_pauses_briefly():
    b, clock = make()
    b.observe_rate_limit("", None)
    with pytest.raises(BudgetExhausted):
        b.reserve(1)
    clock.t += 6
    b.reserve(1)


def test_garbage_in_a_429_body_is_ignored():
    b, _ = make(tokens_per_day=1000)
    b.observe_rate_limit("(TPD): Limit lots, Used many <script>", None)
    assert b.snapshot()["tokens_used_24h"] == 0


# ------------------------------------------------------------------ persistence
def test_ledger_survives_a_restart_and_the_file_is_private(tmp_path):
    path = tmp_path / "d" / "b.json"
    clock = Clock()
    b = CloudBudget(Limits(tokens_per_day=1000, safety=1.0), path=path, clock=clock)
    b.commit(b.reserve(700), 700)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = CloudBudget(Limits(tokens_per_day=1000, safety=1.0), path=path, clock=clock)
    assert again.snapshot()["tokens_used_24h"] == 700
    with pytest.raises(BudgetExhausted):
        again.reserve(400)
    clock.t += DAY_S + 1
    assert (
        CloudBudget(Limits(tokens_per_day=1000), path=path, clock=clock).snapshot()[
            "tokens_used_24h"
        ]
        == 0
    )


@pytest.mark.parametrize("content", ["not json", "{}", '{"tokens": "x", "requests": []}', ""])
def test_corrupt_ledger_file_starts_empty_instead_of_crashing(tmp_path, content):
    path = tmp_path / "b.json"
    path.write_text(content)
    assert CloudBudget(Limits(tokens_per_day=10), path=path).snapshot()["tokens_used_24h"] == 0


def test_future_dated_entries_cannot_pin_the_budget(tmp_path):
    path = tmp_path / "b.json"
    clock = Clock()
    path.write_text(json.dumps({"v": 1, "tokens": [[clock.t + 10**7, 10**6]], "requests": []}))
    assert (
        CloudBudget(Limits(tokens_per_day=100), path=path, clock=clock).snapshot()[
            "tokens_used_24h"
        ]
        == 0
    )


def test_unwritable_ledger_does_not_break_requests(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    b = CloudBudget(Limits(tokens_per_day=1000), path=blocker / "sub" / "b.json")
    b.commit(b.reserve(10), 10)  # parent is a file: save fails, accounting continues
    assert b.snapshot()["tokens_used_24h"] == 10


# ------------------------------------------------------------------ the wrapper
async def test_over_budget_request_never_reaches_the_provider():
    inner = FakeAdapter("cloud")
    b, _ = make(tokens_per_day=100)
    wrapped = BudgetedAdapter(inner, b)
    with pytest.raises(BudgetExhausted):
        await wrapped.complete(req("x" * 2000, max_out=500))
    assert inner.requests == []  # no network call was made


async def test_router_answers_locally_when_the_budget_is_gone():
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    b, _ = make(tokens_per_day=100)
    router = ModelRouter(
        cloud=BudgetedAdapter(cloud, b), local=local, policy="cloud_first", enforce_style=False
    )
    out = await router.complete(
        [ChatMessage("user", "hello there")], build_system_prompt=lambda ctx: "s"
    )
    assert out.response.tier == "local" and out.route.fallback
    assert out.route.attempts[0].error_kind == "budget"
    assert cloud.requests == []


async def test_local_only_requests_never_touch_the_budget():
    b, _ = make(tokens_per_day=10**6)
    router = ModelRouter(
        cloud=BudgetedAdapter(FakeAdapter("cloud"), b), local=FakeAdapter("local"), policy="auto"
    )
    await router.complete(
        [ChatMessage("user", "secret")], build_system_prompt=lambda c: "s", privacy="local_only"
    )
    assert b.snapshot()["tokens_used_24h"] == 0 and b.snapshot()["requests_24h"] == 0


async def test_success_records_real_usage_not_the_estimate():
    b, _ = make(tokens_per_day=10**6)
    wrapped = BudgetedAdapter(FakeAdapter("cloud"), b)  # the fake reports 11 + 5 tokens
    await wrapped.complete(req())
    assert b.snapshot()["tokens_used_24h"] == 16 and b.snapshot()["requests_24h"] == 1


async def test_provider_failure_without_a_429_gives_the_reservation_back():
    b, _ = make(tokens_per_day=10**6)
    wrapped = BudgetedAdapter(FakeAdapter("cloud", error=ProviderUnavailable("503")), b)
    with pytest.raises(ProviderUnavailable):
        await wrapped.complete(req())
    s = b.snapshot()
    assert s["tokens_used_24h"] == 0 and s["requests_24h"] == 0 and s["cooldown_s"] == 0
    b.reserve(10**6 - 1)  # nothing is still held


async def test_a_429_from_the_provider_sets_a_cooldown_and_syncs_the_ledger():
    b, _ = make(tokens_per_day=200_000)
    err = ProviderRateLimited("429", body=GROQ_429, retry_after_s=60)
    wrapped = BudgetedAdapter(FakeAdapter("cloud", error=err), b)
    with pytest.raises(ProviderRateLimited):
        await wrapped.complete(req())
    assert b.snapshot()["tokens_used_24h"] == 199_497 and b.snapshot()["cooldown_s"] >= 59
    with pytest.raises(BudgetExhausted):
        await wrapped.complete(req())


async def test_short_per_minute_shortfall_is_waited_out_long_ones_are_not():
    b = CloudBudget(Limits(tokens_per_minute=1000, safety=1.0))  # real clock
    inner = FakeAdapter("cloud")
    wrapped = BudgetedAdapter(inner, b, max_wait_s=0.3)
    b._tokens.append((b._clock() - 59.8, 900))  # frees up in ~0.2 s
    await wrapped.complete(req(max_out=50))
    assert len(inner.requests) == 1
    b._tokens.append((b._clock() - 10, 990))  # leaves no room; would take ~50 s
    with pytest.raises(BudgetExhausted):
        await wrapped.complete(req(max_out=50))
    assert len(inner.requests) == 1


async def test_streaming_commits_actual_usage_and_passes_chunks_through():
    b, _ = make(tokens_per_day=10**6)
    wrapped = BudgetedAdapter(StreamingFake("cloud"), b)
    items = [i async for i in wrapped.stream(req())]
    assert "".join(i.text for i in items[:-1]) == "Hello there. Done."
    assert b.snapshot()["requests_24h"] == 1 and b.snapshot()["tokens_used_24h"] > 0


async def test_abandoned_stream_is_still_charged_and_nothing_stays_reserved():
    b, _ = make(tokens_per_day=10**6)
    wrapped = BudgetedAdapter(StreamingFake("cloud"), b)
    gen = wrapped.stream(req("x" * 320))
    await gen.__anext__()  # one chunk arrives, then the client disconnects
    await gen.aclose()
    s = b.snapshot()
    assert s["requests_24h"] == 1 and s["tokens_used_24h"] >= 80
    assert b._held_tokens == 0 and b._held_requests == 0


async def test_stream_over_budget_refuses_before_connecting():
    inner = StreamingFake("cloud")
    wrapped = BudgetedAdapter(inner, make(tokens_per_day=10)[0])
    with pytest.raises(BudgetExhausted):
        async for _ in wrapped.stream(req("x" * 1000)):
            pass
    assert inner.requests == []


async def test_concurrent_requests_never_exceed_the_cap():
    b, _ = make(tokens_per_day=2000)
    inner = SlowFake("cloud")  # holds its reservation while in flight, like a real network call
    wrapped = BudgetedAdapter(inner, b)
    big = req("x" * 1600, max_out=100)  # estimate ~600 tokens each
    results = await asyncio.gather(
        *(wrapped.complete(big) for _ in range(10)), return_exceptions=True
    )
    admitted = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, BudgetExhausted)]
    assert len(admitted) == 3 and len(refused) == 7 and len(inner.requests) == 3


def test_estimate_is_not_wildly_low():
    assert estimate_tokens(req("a" * 3200)) >= 1000


async def test_health_mentions_usage_without_probing():
    b, _ = make(tokens_per_day=1000)
    b.commit(b.reserve(10), 10)
    h = await BudgetedAdapter(FakeAdapter("cloud"), b).health()
    assert "10/1000" in h.detail


# ------------------------------------------------------------------ the real HTTP adapter
def _http_adapter(handler) -> OpenAICompatAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatAdapter(client=client, base_url="http://x/v1", api_key="k", model="m")


async def test_http_429_carries_body_and_retry_after():
    def handler(request):
        return httpx.Response(429, text=GROQ_429, headers={"retry-after": "484"})

    with pytest.raises(ProviderRateLimited) as e:
        await _http_adapter(handler).complete(req())
    assert e.value.retry_after_s == 484 and "TPD" in e.value.body
    assert "k" not in str(e.value).replace("HTTP", "")  # message holds no key material


async def test_http_429_on_a_stream_carries_the_same_details():
    def handler(request):
        return httpx.Response(429, text=GROQ_429, headers={"retry-after": "12"})

    with pytest.raises(ProviderRateLimited) as e:
        async for _ in _http_adapter(handler).stream(req()):
            pass
    assert e.value.retry_after_s == 12


async def test_http_429_with_a_junk_retry_after_header():
    def handler(request):
        return httpx.Response(429, text="slow down", headers={"retry-after": "soon"})

    with pytest.raises(ProviderRateLimited) as e:
        await _http_adapter(handler).complete(req())
    assert e.value.retry_after_s is None


def test_old_entries_are_dropped_from_memory_not_just_ignored():
    b, clock = make(tokens_per_day=10**9)
    for _ in range(100):
        b.commit(b.reserve(1), 1)
    clock.t += DAY_S + 1
    b.snapshot()
    assert len(b._tokens) == 0 and len(b._requests) == 0
