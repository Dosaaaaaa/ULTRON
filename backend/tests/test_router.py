from __future__ import annotations

import asyncio

import pytest

from app.models.base import ChatMessage, ModelAdapter, ProviderTimeout, ProviderUnavailable
from app.models.router import AllModelsFailed, ModelRouter, classify_complexity
from tests.fakes import FakeAdapter, down

HISTORY = [ChatMessage("user", "hello")]


def prompt(ctx):
    return f"SYSTEM tier={ctx.tier} degraded={ctx.degraded} local_only={ctx.local_only}"


async def route(router, *, privacy="default", complexity="simple"):
    return await router.complete(
        HISTORY, build_system_prompt=prompt, privacy=privacy, complexity=complexity
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hi", "simple"),
        ("what time is it", "simple"),
        ("Analyze the tradeoffs of this plan", "complex"),
        ("why did that fail", "complex"),
        ("a? b?", "complex"),
        ("x" * 401, "complex"),
    ],
)
def test_complexity_heuristic(text, expected):
    assert classify_complexity(text) == expected


async def test_auto_policy_routes_by_complexity():
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    router = ModelRouter(cloud=cloud, local=local, policy="auto")
    assert (await route(router, complexity="complex")).response.tier == "cloud"
    assert (await route(router, complexity="simple")).response.tier == "local"


async def test_explicit_policies():
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    cf = ModelRouter(cloud=cloud, local=local, policy="cloud_first")
    lf = ModelRouter(cloud=cloud, local=local, policy="local_first")
    lo = ModelRouter(cloud=cloud, local=local, policy="local_only")
    assert (await route(cf)).response.tier == "cloud"
    assert (await route(lf, complexity="complex")).response.tier == "local"
    assert (await route(lo, complexity="complex")).response.tier == "local"


async def test_cloud_failure_falls_back_to_local_and_says_so():
    cloud, local = down("cloud"), FakeAdapter("local", model="qwen3:4b")
    out = await route(ModelRouter(cloud=cloud, local=local), complexity="complex")
    r = out.route
    assert out.response.tier == "local"
    assert r.fallback and r.degraded
    assert [(a.tier, a.ok, a.error_kind) for a in r.attempts] == [
        ("cloud", False, "unavailable"),
        ("local", True, None),
    ]
    assert "less capable" in r.notice and "fake-cloud" in r.notice
    # the local engine was told it is degraded, truthfully
    assert "degraded=True" in local.requests[0].messages[0].content


async def test_local_failure_falls_back_to_cloud_without_degradation_flag():
    out = await route(ModelRouter(cloud=FakeAdapter("cloud"), local=down("local")))
    assert out.response.tier == "cloud" and out.route.fallback and not out.route.degraded
    assert "Local engine" in out.route.notice


async def test_local_only_request_never_reaches_cloud_even_when_local_fails():
    cloud = FakeAdapter("cloud")
    router = ModelRouter(cloud=cloud, local=down("local"))
    with pytest.raises(AllModelsFailed) as info:
        await route(router, privacy="local_only", complexity="complex")
    assert cloud.requests == []
    assert [a.tier for a in info.value.route.attempts] == ["local"]


async def test_allow_cloud_false_is_a_hard_switch():
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    router = ModelRouter(cloud=cloud, local=local, policy="cloud_first", allow_cloud=False)
    out = await route(router, complexity="complex")
    assert out.response.tier == "local" and cloud.requests == []
    assert out.route.reason == "cloud disabled by configuration"
    assert "local_only=True" in local.requests[0].messages[0].content


async def test_local_only_policy_marks_prompt_as_on_device():
    local = FakeAdapter("local")
    await route(ModelRouter(cloud=FakeAdapter("cloud"), local=local, policy="local_only"))
    assert "local_only=True" in local.requests[0].messages[0].content


async def test_all_models_fail_raises_with_attempts():
    router = ModelRouter(cloud=down("cloud"), local=down("local"))
    with pytest.raises(AllModelsFailed) as info:
        await route(router, complexity="complex")
    assert len(info.value.route.attempts) == 2
    assert not any(a.ok for a in info.value.route.attempts)


async def test_no_adapters_configured():
    with pytest.raises(AllModelsFailed) as info:
        await route(ModelRouter(cloud=None, local=None))
    assert "no eligible model" in info.value.route.reason


async def test_unconfigured_cloud_means_local_only_without_error():
    out = await route(ModelRouter(cloud=None, local=FakeAdapter("local")), complexity="complex")
    assert out.response.tier == "local" and not out.route.fallback


async def test_hung_adapter_is_cut_off_by_outer_deadline():
    class Hang(FakeAdapter):
        async def complete(self, request):
            await asyncio.sleep(30)

    hung = Hang("cloud", timeout_s=0.05)
    hung.timeout_s = -1.9  # outer deadline = timeout_s + 2 = 0.1s
    out = await route(ModelRouter(cloud=hung, local=FakeAdapter("local")), complexity="complex")
    assert out.response.tier == "local"
    assert out.route.attempts[0].error_kind == ProviderTimeout.kind


async def test_adapter_timeout_error_triggers_fallback():
    cloud = FakeAdapter("cloud", error=ProviderTimeout("t"))
    out = await route(ModelRouter(cloud=cloud, local=FakeAdapter("local")), complexity="complex")
    assert out.route.attempts[0].error_kind == "timeout" and out.response.tier == "local"


async def test_usage_tracks_latency_tokens_and_failures():
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    router = ModelRouter(cloud=cloud, local=local, policy="cloud_first")
    await route(router)
    await route(router)
    cloud.error = ProviderUnavailable("x")
    await route(router)  # fails over to local
    u = router.usage()
    assert u["fake-cloud/cloud-model"] == {
        "requests": 3,
        "failures": 1,
        "avg_latency_ms": 7,
        "prompt_tokens": 22,
        "completion_tokens": 10,
    }
    assert u["fake-local/local-model"]["requests"] == 1


async def test_system_prompt_is_first_message_and_history_follows():
    cloud = FakeAdapter("cloud")
    await route(ModelRouter(cloud=cloud, local=None, policy="cloud_first"))
    msgs = cloud.requests[0].messages
    assert msgs[0].role == "system" and msgs[0].content.startswith("SYSTEM tier=cloud")
    assert msgs[1:] == HISTORY


def test_adapter_type_is_abstract():
    with pytest.raises(TypeError):
        ModelAdapter()  # type: ignore[abstract]
