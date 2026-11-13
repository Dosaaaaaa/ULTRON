"""Adapters against httpx.MockTransport: request shape, response parsing, error mapping."""

from __future__ import annotations

import json

import httpx
import pytest

from app.models.base import (
    ChatMessage,
    ModelRequest,
    ProviderBadResponse,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.models.ollama import OllamaAdapter
from app.models.openai_compat import OpenAICompatAdapter

REQ = ModelRequest([ChatMessage("system", "s"), ChatMessage("user", "hi")], 100, 0.5)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def cloud(handler) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(
        client=client_for(handler),
        base_url="https://api.example.test/v1/",
        api_key="sk-SECRETSECRETSECRET",
        model="m1",
        provider="acme",
    )


def local(handler, **kw) -> OllamaAdapter:
    return OllamaAdapter(
        client=client_for(handler), base_url="http://ollama.test:11434", model="qwen3:4b", **kw
    )


async def test_openai_compat_success_and_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "m1-2026",
                "choices": [{"message": {"content": "  hello  "}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3},
            },
        )

    r = await cloud(handler).complete(REQ)
    assert seen["url"] == "https://api.example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-SECRETSECRETSECRET"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "s"}
    assert seen["body"]["max_tokens"] == 100 and seen["body"]["stream"] is False
    assert (r.content, r.provider, r.model, r.tier) == ("hello", "acme", "m1-2026", "cloud")
    assert (r.prompt_tokens, r.completion_tokens) == (9, 3)


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ProviderRejected),
        (403, ProviderRejected),
        (400, ProviderRejected),
        (429, ProviderUnavailable),
        (500, ProviderUnavailable),
        (503, ProviderUnavailable),
    ],
)
async def test_openai_compat_status_mapping(status, exc):
    with pytest.raises(exc):
        await cloud(lambda r: httpx.Response(status, text="nope")).complete(REQ)


@pytest.mark.parametrize(
    "body",
    [{"choices": []}, {"choices": [{"message": {"content": ""}}]}, {"x": 1}],
)
async def test_openai_compat_bad_body(body):
    with pytest.raises(ProviderBadResponse):
        await cloud(lambda r: httpx.Response(200, json=body)).complete(REQ)


async def test_openai_compat_timeout_and_connect_errors():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderTimeout):
        await cloud(timeout).complete(REQ)
    with pytest.raises(ProviderUnavailable):
        await cloud(refused).complete(REQ)


async def test_openai_compat_error_messages_never_contain_the_key():
    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (refused, lambda r: httpx.Response(401, text="bad")):
        with pytest.raises(Exception) as info:
            await cloud(handler).complete(REQ)
        assert "SECRETSECRET" not in str(info.value)


async def test_ollama_success_strips_think_blocks_and_sends_think_flag():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {"content": "<think>hmm\nplanning</think>\nAnswer."},
                "prompt_eval_count": 20,
                "eval_count": 4,
            },
        )

    r = await local(handler).complete(REQ)
    assert r.content == "Answer."
    assert (r.tier, r.provider, r.model) == ("local", "ollama", "qwen3:4b")
    assert (r.prompt_tokens, r.completion_tokens) == (20, 4)
    assert seen["body"]["think"] is False and seen["body"]["stream"] is False
    assert seen["body"]["options"] == {"temperature": 0.5, "num_predict": 100}


async def test_ollama_errors():
    with pytest.raises(ProviderRejected, match="ollama pull"):
        await local(lambda r: httpx.Response(404, json={"error": "model not found"})).complete(REQ)
    with pytest.raises(ProviderUnavailable):
        await local(lambda r: httpx.Response(500)).complete(REQ)
    with pytest.raises(ProviderBadResponse):  # only a think block => nothing to show
        await local(
            lambda r: httpx.Response(200, json={"message": {"content": "<think>x</think>"}})
        ).complete(REQ)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderUnavailable):
        await local(refused).complete(REQ)


async def test_ollama_health_states():
    up = await local(
        lambda r: httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
    ).health()
    assert up.reachable is True and up.detail == "model available"

    missing = await local(
        lambda r: httpx.Response(200, json={"models": [{"name": "moondream:latest"}]})
    ).health()
    assert missing.reachable is True and "ollama pull qwen3:4b" in missing.detail

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    assert (await local(refused).health()).reachable is False


async def test_cloud_health_never_probes_the_network():
    def boom(request):
        raise AssertionError("cloud health must not make a request")

    h = await cloud(boom).health()
    assert h.reachable is None and h.configured is True


async def test_ollama_strips_headless_reasoning_prefix():
    """Regression (found in live Qwen3 run): think=false => reasoning, then a bare </think>."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "Okay, the user asks X.\nLet me think.\n</think>\n\n391"}},
        )

    r = await local(handler).complete(REQ)
    assert r.content == "391"
