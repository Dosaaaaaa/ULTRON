from __future__ import annotations

import json

import httpx
import pytest

from app.models.base import (
    ChatMessage,
    ModelRequest,
    ModelResponse,
    ProviderBadResponse,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    StreamChunk,
)
from app.models.ollama import OllamaAdapter
from app.models.openai_compat import OpenAICompatAdapter

REQ = ModelRequest([ChatMessage("system", "s"), ChatMessage("user", "hi")], 100, 0.5)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ollama(handler) -> OllamaAdapter:
    return OllamaAdapter(
        client=client_for(handler), base_url="http://o.test", model="m", timeout_s=5
    )


def cloud(handler) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(
        client=client_for(handler),
        base_url="https://c.test/v1",
        api_key="sk-SECRETSECRET",
        model="m1",
        provider="acme",
        timeout_s=5,
    )


async def collect(adapter):
    return [item async for item in adapter.stream(REQ)]


def ndjson(*objs) -> bytes:
    return ("\n".join(json.dumps(o) for o in objs) + "\n").encode()


# ---- Ollama ------------------------------------------------------------------------
async def test_ollama_stream_chunks_then_final_response_with_usage():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo."}, "done": False},
                {"message": {"content": ""}, "done": True, "prompt_eval_count": 9, "eval_count": 3},
            ),
        )

    items = await collect(ollama(handler))
    assert [i.text for i in items[:-1]] == ["Hel", "lo."]
    assert all(isinstance(i, StreamChunk) for i in items[:-1])
    final = items[-1]
    assert isinstance(final, ModelResponse)
    assert (final.content, final.prompt_tokens, final.completion_tokens) == ("Hello.", 9, 3)
    assert seen["body"]["stream"] is True and seen["body"]["think"] is False


async def test_ollama_stream_errors_before_output():
    with pytest.raises(ProviderRejected, match="ollama pull"):
        await collect(ollama(lambda r: httpx.Response(404, json={"error": "nf"})))
    with pytest.raises(ProviderUnavailable):
        await collect(ollama(lambda r: httpx.Response(503)))

    def refused(request):
        raise httpx.ConnectError("no", request=request)

    with pytest.raises(ProviderUnavailable):
        await collect(ollama(refused))


async def test_ollama_stream_error_line_mid_stream_and_empty_stream():
    body = ndjson({"message": {"content": "part"}, "done": False}, {"error": "model crashed"})
    seen = []
    with pytest.raises(ProviderUnavailable, match="stream error"):
        async for item in ollama(lambda r: httpx.Response(200, content=body)).stream(REQ):
            seen.append(item)
    assert [i.text for i in seen] == ["part"]  # output before the failure was delivered

    with pytest.raises(ProviderBadResponse):
        await collect(ollama(lambda r: httpx.Response(200, content=ndjson({"done": True}))))


# ---- OpenAI-compatible SSE -----------------------------------------------------------
def sse(*events) -> bytes:
    lines = [f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events]
    return "".join(lines).encode()


async def test_openai_stream_sse_chunks_usage_and_request_shape():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=sse(
                {"model": "m1-x", "choices": [{"delta": {"role": "assistant"}}]},
                {"choices": [{"delta": {"content": "Hi "}}]},
                {"choices": [{"delta": {"content": "you."}}]},
                {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
                "[DONE]",
            ),
        )

    items = await collect(cloud(handler))
    assert [i.text for i in items[:-1]] == ["Hi ", "you."]
    final = items[-1]
    assert (final.content, final.model, final.tier) == ("Hi you.", "m1-x", "cloud")
    assert (final.prompt_tokens, final.completion_tokens) == (4, 2)
    assert seen["auth"] == "Bearer sk-SECRETSECRET"
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {
        "include_usage": True
    }


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ProviderRejected),
        (403, ProviderRejected),
        (400, ProviderRejected),
        (429, ProviderUnavailable),
        (500, ProviderUnavailable),
    ],
)
async def test_openai_stream_status_mapping(status, exc):
    with pytest.raises(exc) as info:
        await collect(cloud(lambda r: httpx.Response(status, text="no")))
    assert "SECRETSECRET" not in str(info.value)


async def test_openai_stream_bad_line_empty_and_timeout():
    with pytest.raises(ProviderBadResponse):
        await collect(cloud(lambda r: httpx.Response(200, content=sse("{not json"))))
    with pytest.raises(ProviderBadResponse):
        await collect(cloud(lambda r: httpx.Response(200, content=sse("[DONE]"))))

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderTimeout):
        await collect(cloud(slow))


async def test_default_stream_wraps_complete_for_adapters_without_streaming():
    from tests.fakes import FakeAdapter  # noqa: PLC0415

    items = await collect(FakeAdapter("local", reply="whole answer"))
    assert [type(i).__name__ for i in items] == ["StreamChunk", "ModelResponse"]
    assert items[0].text == "whole answer" and items[1].content == "whole answer"


# ---- reasoning traces must not reach the operator (review finding) ---------------------
def _chunks(*texts):
    objs = [{"message": {"content": t}, "done": False} for t in texts]
    objs.append({"message": {"content": ""}, "done": True})
    return ndjson(*objs)


async def _visible(*texts):
    items = await collect(ollama(lambda r: httpx.Response(200, content=_chunks(*texts))))
    return "".join(i.text for i in items[:-1]), items[-1].content


async def test_ollama_stream_drops_a_think_block_even_when_tags_are_split_across_chunks():
    live, final = await _visible("<th", "ink>plan the ", "answer</thi", "nk>\n\nForty", "-two.")
    assert live.strip() == "Forty-two." and final == "Forty-two."
    assert "plan" not in live


async def test_ollama_stream_never_shows_the_bare_closing_tag_and_the_stored_text_is_clean():
    # Qwen3 with think=false can reason inline and then close with a bare </think>.
    live, final = await _visible("Okay, let me work this out.\n", "</thi", "nk>\n\n391")
    assert "</think>" not in live and "</thi" not in live
    assert final == "391"  # what is stored/returned never holds the reasoning


async def test_ollama_stream_plain_text_is_unchanged_and_a_lone_less_than_is_kept():
    live, final = await _visible("2 < 3 and ", "x<y", " is true.")
    assert live == "2 < 3 and x<y is true." and final == live
