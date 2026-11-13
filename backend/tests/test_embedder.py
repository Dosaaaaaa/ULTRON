"""OllamaEmbedder against a mocked HTTP transport: request shape, prefixes, error mapping."""

from __future__ import annotations

import json
import math

import httpx
import pytest

from app.memory.embedder import EmbeddingUnavailable, OllamaEmbedder


def make(handler, model="nomic-embed-text") -> OllamaEmbedder:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaEmbedder(client, "http://ollama.test", model, timeout_s=1.0)


def ok(vectors):
    return httpx.Response(200, json={"embeddings": vectors})


async def test_nomic_gets_task_prefixes_documents_and_queries_differ():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return ok([[3.0, 4.0]] * len(body["input"]))

    e = make(handler)
    await e.embed(["a fact"])
    await e.embed(["a question"], query=True)
    assert seen[0]["input"] == ["search_document: a fact"]
    assert seen[1]["input"] == ["search_query: a question"]
    assert seen[0]["model"] == "nomic-embed-text"


async def test_other_models_get_no_prefix():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["input"])
        return ok([[1.0, 0.0]])

    await make(handler, model="all-minilm").embed(["plain"], query=True)
    assert seen == [["plain"]]


async def test_vectors_are_normalised_to_unit_length():
    vec = (await make(lambda r: ok([[3.0, 4.0]])).embed(["x"]))[0]
    assert vec == pytest.approx([0.6, 0.8]) and math.isclose(sum(v * v for v in vec), 1.0)


async def test_it_posts_to_the_ollama_embed_endpoint_only():
    paths = []

    def handler(request):
        paths.append((request.method, request.url.host, request.url.path))
        return ok([[1.0]])

    await make(handler).embed(["x"])
    assert paths == [("POST", "ollama.test", "/api/embed")]  # nothing else is contacted


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(404, json={"error": "model not found"}), "model_missing"),
        (httpx.Response(500, text="boom"), "bad_response"),
        (httpx.Response(200, json={"nope": 1}), "bad_response"),
        (httpx.Response(200, json={"embeddings": []}), "bad_response"),
        (httpx.Response(200, json={"embeddings": [[]]}), "bad_response"),
        (httpx.Response(200, json={"embeddings": [["a"]]}), "bad_response"),
        (httpx.Response(200, text="not json"), "bad_response"),
    ],
)
async def test_bad_replies_become_a_typed_unavailable_error(response, kind):
    with pytest.raises(EmbeddingUnavailable) as exc:
        await make(lambda r: response).embed(["x"])
    assert exc.value.kind == kind


async def test_network_failures_are_mapped():
    def refuse(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(EmbeddingUnavailable) as exc:
        await make(refuse).embed(["x"])
    assert exc.value.kind == "unreachable"
    with pytest.raises(EmbeddingUnavailable) as exc:
        await make(slow).embed(["x"])
    assert exc.value.kind == "timeout"


async def test_a_wrong_number_of_vectors_is_rejected():
    with pytest.raises(EmbeddingUnavailable):
        await make(lambda r: ok([[1.0], [2.0]])).embed(["only one text"])


async def test_available_checks_the_model_list_without_embedding():
    def tags(models):
        return lambda r: httpx.Response(200, json={"models": [{"name": m} for m in models]})

    assert await make(tags(["nomic-embed-text:latest"])).available() is True
    assert await make(tags(["qwen3:4b"])).available() is False
    assert await make(lambda r: httpx.Response(500)).available() is False

    def down(request):
        raise httpx.ConnectError("refused")

    assert await make(down).available() is False
