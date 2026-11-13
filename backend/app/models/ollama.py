"""Local adapter for Ollama's /api/chat endpoint."""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator

import httpx

from app.models.base import (
    AdapterHealth,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ProviderBadResponse,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    StreamChunk,
)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN, _CLOSE = "<think>", "</think>"


def _clean(content: str) -> str:
    """The text an operator may see: no reasoning trace, whichever way the model wrote it."""
    # Some builds inline reasoning traces; the spoken/visible answer must not contain them.
    content = _THINK_BLOCK.sub("", content or "")
    # Qwen3 with think=false still reasons inline and closes with a bare </think> (no opener).
    if _CLOSE in content:
        content = content.rsplit(_CLOSE, 1)[1]
    return content.strip()


def _partial_tag_len(text: str) -> int:
    """Length of a trailing piece of `text` that could still become <think> or </think>."""
    for n in range(min(len(text), len(_CLOSE) - 1), 0, -1):
        tail = text[-n:]
        if _OPEN.startswith(tail) or _CLOSE.startswith(tail):
            return n
    return 0


class _ThinkFilter:
    """Streaming version of `_clean` for the live text: drops <think>...</think> blocks and a bare
    </think>, however the tags are split across chunks. A reasoning trace that is closed only by a
    bare </think> cannot be recalled once it has been sent (nothing marks it until the closer
    arrives), so only the stored/returned text, built by `_clean`, is guaranteed free of it."""

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while True:
            if self._inside:
                end = self._buf.find(_CLOSE)
                if end < 0:
                    self._buf = self._buf[-(len(_CLOSE) - 1) :]  # keep only a possible partial tag
                    break
                self._buf, self._inside = self._buf[end + len(_CLOSE) :], False
                continue
            start, bare = self._buf.find(_OPEN), self._buf.find(_CLOSE)
            hits = [i for i in (start, bare) if i >= 0]
            if not hits:
                keep = _partial_tag_len(self._buf)
                out.append(self._buf[: len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep :]
                break
            first = min(hits)
            out.append(self._buf[:first])
            if first == start:
                self._buf, self._inside = self._buf[start + len(_OPEN) :], True
            else:  # a bare closer: drop only the tag
                self._buf = self._buf[bare + len(_CLOSE) :]
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = ("" if self._inside else self._buf), ""
        return rest


class OllamaAdapter(ModelAdapter):
    tier = "local"
    provider = "ollama"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        timeout_s: float = 120.0,
        think: bool = False,
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self._think = think

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": False,
            "think": self._think,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
            },
        }
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                f"{self._base}/api/chat", json=payload, timeout=self.timeout_s
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"ollama timed out after {self.timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code == 404:
            raise ProviderRejected(
                f"ollama model '{self.model}' not found; run: ollama pull {self.model}"
            )
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"ollama HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderRejected(f"ollama HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
            content = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderBadResponse("ollama returned an unparseable body") from exc
        content = _clean(content)
        if not content:
            raise ProviderBadResponse("ollama returned empty content")

        return ModelResponse(
            content=content,
            provider=self.provider,
            model=self.model,
            tier="local",
            latency_ms=latency_ms,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk | ModelResponse]:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": True,
            "think": self._think,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
            },
        }
        timeout = httpx.Timeout(self.timeout_s, connect=5.0)
        started = time.perf_counter()
        parts: list[str] = []
        think = _ThinkFilter()
        final: dict = {}
        try:
            async with self._client.stream(
                "POST", f"{self._base}/api/chat", json=payload, timeout=timeout
            ) as resp:
                if resp.status_code == 404:
                    raise ProviderRejected(
                        f"ollama model '{self.model}' not found; run: ollama pull {self.model}"
                    )
                if resp.status_code >= 500:
                    raise ProviderUnavailable(f"ollama HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:200]
                    raise ProviderRejected(f"ollama HTTP {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError as exc:
                        raise ProviderBadResponse("ollama sent an unparseable stream line") from exc
                    if "error" in data:
                        raise ProviderUnavailable(
                            f"ollama stream error: {str(data['error'])[:120]}"
                        )
                    text = (data.get("message") or {}).get("content") or ""
                    if text:
                        parts.append(text)
                        visible = think.feed(text)
                        if visible:
                            yield StreamChunk(visible)
                    if data.get("done"):
                        final = data
                tail = think.flush()
                if tail:
                    yield StreamChunk(tail)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"ollama timed out after {self.timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc
        content = _clean("".join(parts))
        if not content:
            raise ProviderBadResponse("ollama returned empty content")
        yield ModelResponse(
            content=content,
            provider=self.provider,
            model=self.model,
            tier="local",
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=final.get("prompt_eval_count"),
            completion_tokens=final.get("eval_count"),
        )

    async def health(self) -> AdapterHealth:
        def result(reachable: bool, detail: str) -> AdapterHealth:
            return AdapterHealth("ollama", self.model, "local", True, reachable, detail)

        try:
            resp = await self._client.get(f"{self._base}/api/tags", timeout=3.0)
            resp.raise_for_status()
            names = {m.get("name", "") for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return result(False, "ollama not reachable")
        wanted = {self.model, self.model if ":" in self.model else f"{self.model}:latest"}
        if wanted & names:
            return result(True, "model available")
        return result(True, f"ollama up but model missing; run: ollama pull {self.model}")
