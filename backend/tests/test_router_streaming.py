from __future__ import annotations

import pytest

from app.models.base import ChatMessage, ProviderTimeout, ProviderUnavailable, StreamChunk
from app.models.router import (
    AllModelsFailed,
    ModelRouter,
    RoutedResponse,
    StreamFailed,
    StreamStarted,
)
from tests.fakes import StreamingFake

HISTORY = [ChatMessage("user", "hello")]


def prompt(ctx):
    return f"SYS tier={ctx.tier} voice={ctx.voice} degraded={ctx.degraded}"


async def run(router, **kw):
    kw.setdefault("complexity", "simple")
    return [i async for i in router.stream(HISTORY, build_system_prompt=prompt, **kw)]


async def test_stream_event_order_started_chunks_final():
    local = StreamingFake("local")
    events = await run(ModelRouter(cloud=None, local=local))
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["StreamStarted", "StreamChunk", "StreamChunk", "StreamChunk", "RoutedResponse"]
    started, final = events[0], events[-1]
    assert isinstance(started, StreamStarted) and started.tier == "local"
    assert isinstance(final, RoutedResponse) and final.response.content == "Hello there. Done."
    assert final.route.attempts[-1].ok and final.route.attempts[-1].latency_ms == 7


async def test_fallback_happens_before_first_chunk():
    cloud = StreamingFake("cloud", error=ProviderUnavailable("down"))  # fails immediately
    local = StreamingFake("local", model="qwen")
    events = await run(ModelRouter(cloud=cloud, local=local), complexity="complex")
    started = events[0]
    assert started.tier == "local" and started.route.fallback and started.route.degraded
    assert "less capable" in started.route.notice
    assert [a.ok for a in events[-1].route.attempts] == [False, True]
    assert cloud.closed and local.closed


async def test_failure_after_output_is_not_silently_retried_elsewhere():
    cloud = StreamingFake("cloud", error=ProviderTimeout("t"), fail_at=1)  # 1 chunk, then dies
    local = StreamingFake("local")
    got = []
    with pytest.raises(StreamFailed) as info:
        async for e in ModelRouter(cloud=cloud, local=local, policy="cloud_first").stream(
            HISTORY, build_system_prompt=prompt
        ):
            got.append(e)
    assert info.value.partial == "Hello " and info.value.kind == "timeout"
    assert local.requests == []  # a second engine never saw the request
    assert [type(e).__name__ for e in got] == ["StreamStarted", "StreamChunk"]


async def test_local_only_stream_never_touches_cloud():
    cloud = StreamingFake("cloud")
    local = StreamingFake("local", error=ProviderUnavailable("down"))
    with pytest.raises(AllModelsFailed):
        await run(ModelRouter(cloud=cloud, local=local), privacy="local_only", complexity="complex")
    assert cloud.requests == []


async def test_all_down_and_none_configured():
    router = ModelRouter(
        cloud=StreamingFake("cloud", error=ProviderUnavailable("x")),
        local=StreamingFake("local", error=ProviderUnavailable("x")),
    )
    with pytest.raises(AllModelsFailed) as info:
        await run(router, complexity="complex")
    assert len(info.value.route.attempts) == 2
    with pytest.raises(AllModelsFailed):
        await run(ModelRouter(cloud=None, local=None))


async def test_closing_the_stream_early_closes_the_upstream_generator():
    """Barge-in relies on this: dropping the consumer must stop the engine."""
    local = StreamingFake("local")
    gen = ModelRouter(cloud=None, local=local).stream(HISTORY, build_system_prompt=prompt)
    first = await gen.__anext__()
    assert isinstance(first, StreamStarted)
    await gen.aclose()
    assert local.closed


async def test_voice_flag_reaches_the_prompt_builder():
    local = StreamingFake("local")
    await run(ModelRouter(cloud=None, local=local), voice=True)
    assert "voice=True" in local.requests[0].messages[0].content


async def test_stream_records_usage():
    router = ModelRouter(cloud=None, local=StreamingFake("local"))
    await run(router)
    assert router.usage()["fake-local/local-model"]["requests"] == 1


def test_stream_chunk_is_immutable():
    with pytest.raises(AttributeError):
        StreamChunk("a").text = "b"  # type: ignore[misc]
