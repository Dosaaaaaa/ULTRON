"""Mock model adapter for tests: no credentials, no network, no paid requests."""

from __future__ import annotations

import hashlib

from app.models.base import (
    AdapterHealth,
    ModelAdapter,
    ModelError,
    ModelRequest,
    ModelResponse,
    ProviderUnavailable,
    Tier,
)


class FakeAdapter(ModelAdapter):
    def __init__(
        self,
        tier: Tier,
        *,
        provider: str | None = None,
        model: str | None = None,
        reply: str | None = None,
        error: ModelError | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.tier = tier
        self.provider = provider or ("fake-cloud" if tier == "cloud" else "fake-local")
        self.model = model or f"{tier}-model"
        self.timeout_s = timeout_s
        self.reply = reply if reply is not None else f"reply from {self.model}"
        self.error = error
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.error:
            raise self.error
        return ModelResponse(
            content=self.reply,
            provider=self.provider,
            model=self.model,
            tier=self.tier,
            latency_ms=7,
            prompt_tokens=11,
            completion_tokens=5,
        )

    async def health(self) -> AdapterHealth:
        return AdapterHealth(self.provider, self.model, self.tier, True, self.error is None, "fake")


def down(tier: Tier = "cloud") -> FakeAdapter:
    return FakeAdapter(tier, error=ProviderUnavailable("down"))


class StreamingFake(FakeAdapter):
    """Streams `chunks` with an optional delay; can fail before or after output starts."""

    def __init__(
        self,
        tier: Tier,
        *,
        chunks: tuple[str, ...] = ("Hello ", "there. ", "Done."),
        delay: float = 0.0,
        fail_at: int | None = None,  # raise `error` before yielding chunk number fail_at
        **kw,
    ) -> None:
        super().__init__(tier, **kw)
        self.chunks, self.delay, self.fail_at = chunks, delay, fail_at
        self.closed = False

    async def stream(self, request):
        import asyncio  # noqa: PLC0415

        from app.models.base import StreamChunk  # noqa: PLC0415

        self.requests.append(request)
        try:
            if self.error and self.fail_at is None:
                raise self.error
            for i, text in enumerate(self.chunks):
                if self.fail_at == i and self.error:
                    raise self.error
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield StreamChunk(text)
            yield ModelResponse(
                content="".join(self.chunks).strip(),
                provider=self.provider,
                model=self.model,
                tier=self.tier,
                latency_ms=7,
                prompt_tokens=11,
                completion_tokens=5,
            )
        finally:
            self.closed = True


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts that share words are close. No network.

    This tests the plumbing (storage, ranking, filtering, privacy, prompts). It says nothing about
    how good a real embedding model is; that is measured separately against Ollama.
    """

    DIM = 1024  # large enough that unrelated words do not collide into the same bucket

    def __init__(self, model: str = "fake-embed") -> None:
        self.model = model
        self.fail: str | None = None  # set to an EmbeddingUnavailable kind to simulate an outage
        self.calls: list[tuple[str, bool]] = []

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        from app.memory.embedder import EmbeddingUnavailable, normalise

        if self.fail:
            raise EmbeddingUnavailable(self.fail)
        out = []
        for text in texts:
            self.calls.append((text, query))
            vec = [0.0] * self.DIM
            for word in "".join(c.lower() if c.isalnum() else " " for c in text).split():
                if len(word) > 2:  # ignore tiny words so "the/and/is" do not create similarity
                    digest = hashlib.md5(word.encode()).digest()
                    vec[int.from_bytes(digest[:2], "big") % self.DIM] += 1.0
            out.append(normalise(vec) if any(vec) else vec)
        return out

    async def available(self) -> bool:
        return self.fail is None


class FakeSearch:
    """A search provider that records what it was asked and returns scripted results."""

    name = "fake-search"

    def __init__(self, results=None, error: str | None = None) -> None:
        from app.tools.web import WebResult

        self.results = (
            results
            if results is not None
            else [
                WebResult(
                    "Saturn hits 274 moons",
                    "https://example.org/saturn",
                    "Saturn now has 274 confirmed moons.",
                )
            ]
        )
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, count: int):
        from app.tools.web import SearchUnavailable

        self.queries.append(query)
        if self.error:
            raise SearchUnavailable(self.error)
        return list(self.results)[:count]
