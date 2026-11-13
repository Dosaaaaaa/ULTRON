"""Turn text into vectors with a LOCAL model (Ollama). Memory text never leaves the device here."""

from __future__ import annotations

import math
from typing import Protocol

import httpx


class EmbeddingUnavailable(Exception):
    """The embedder could not produce vectors (down, model missing, bad reply)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind  # unreachable | model_missing | timeout | bad_response


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...
    async def available(self) -> bool: ...


def normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class OllamaEmbedder:
    # nomic-embed-text was trained with task prefixes; other models get none.
    _PREFIXES = {"nomic-embed-text": ("search_document: ", "search_query: ")}

    def __init__(
        self, client: httpx.AsyncClient, base_url: str, model: str, timeout_s: float = 4.0
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout_s
        self._doc_prefix, self._query_prefix = next(
            (p for name, p in self._PREFIXES.items() if model.startswith(name)), ("", "")
        )

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        prefix = self._query_prefix if query else self._doc_prefix
        try:
            r = await self._client.post(
                f"{self._base}/api/embed",
                json={"model": self.model, "input": [prefix + t for t in texts]},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingUnavailable("timeout") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable("unreachable") from exc
        if r.status_code == 404:
            raise EmbeddingUnavailable("model_missing")
        if r.status_code != 200:
            raise EmbeddingUnavailable("bad_response")
        try:
            vectors = r.json()["embeddings"]
            if len(vectors) != len(texts) or not all(vectors):
                raise ValueError
            return [normalise([float(x) for x in v]) for v in vectors]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailable("bad_response") from exc

    async def available(self) -> bool:
        """Cheap check (no embedding): is the model installed in Ollama?"""
        try:
            r = await self._client.get(f"{self._base}/api/tags", timeout=min(self._timeout, 3.0))
            names = {m.get("name", "") for m in r.json().get("models", [])}
        except (httpx.HTTPError, ValueError, AttributeError):
            return False
        return any(n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names)
