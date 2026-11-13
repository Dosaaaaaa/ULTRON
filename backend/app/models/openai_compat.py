"""Cloud adapter for any OpenAI-compatible /chat/completions endpoint.

Works with Gemini's OpenAI-compatibility endpoint, OpenAI, OpenRouter, Groq, etc. The base URL,
model and key come from configuration; the key never leaves this object except in the
Authorization header of the outgoing request.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx

from app.models.base import (
    AdapterHealth,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    StreamChunk,
)


def _rate_limited(provider: str, body: str, headers: httpx.Headers) -> ProviderRateLimited:
    retry_after: float | None
    try:
        retry_after = float(headers.get("retry-after", ""))
    except ValueError:
        retry_after = None
    return ProviderRateLimited(f"{provider} HTTP 429", body=body[:2000], retry_after_s=retry_after)


class OpenAICompatAdapter(ModelAdapter):
    tier = "cloud"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str,
        provider: str = "openai-compatible",
        timeout_s: float = 60.0,
    ) -> None:
        self._client = client
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.model = model
        self.provider = provider
        self.timeout_s = timeout_s

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self.provider} timed out after {self.timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailable(f"{self.provider} unreachable: {type(exc).__name__}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code in (401, 403):
            raise ProviderRejected(
                f"{self.provider} rejected credentials (HTTP {resp.status_code})"
            )
        if resp.status_code == 429:
            raise _rate_limited(self.provider, resp.text, resp.headers)
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"{self.provider} HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderRejected(f"{self.provider} HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderBadResponse(f"{self.provider} returned an unparseable body") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderBadResponse(f"{self.provider} returned empty content")

        usage = data.get("usage") or {}
        return ModelResponse(
            content=content.strip(),
            provider=self.provider,
            model=data.get("model") or self.model,
            tier="cloud",
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk | ModelResponse]:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        timeout = httpx.Timeout(self.timeout_s, connect=5.0)
        started = time.perf_counter()
        parts: list[str] = []
        usage: dict = {}
        model = self.model
        try:
            async with self._client.stream(
                "POST",
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout,
            ) as resp:
                if resp.status_code in (401, 403):
                    raise ProviderRejected(
                        f"{self.provider} rejected credentials (HTTP {resp.status_code})"
                    )
                if resp.status_code == 429:
                    text = (await resp.aread()).decode(errors="replace")
                    raise _rate_limited(self.provider, text, resp.headers)
                if resp.status_code >= 500:
                    raise ProviderUnavailable(f"{self.provider} HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:200]
                    raise ProviderRejected(f"{self.provider} HTTP {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except ValueError as exc:
                        raise ProviderBadResponse(
                            f"{self.provider} sent a bad stream line"
                        ) from exc
                    model = event.get("model") or model
                    usage = event.get("usage") or usage
                    for choice in event.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if isinstance(text, str) and text:
                            parts.append(text)
                            yield StreamChunk(text)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self.provider} timed out after {self.timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailable(f"{self.provider} unreachable: {type(exc).__name__}") from exc
        content = "".join(parts).strip()
        if not content:
            raise ProviderBadResponse(f"{self.provider} returned empty content")
        yield ModelResponse(
            content=content,
            provider=self.provider,
            model=model,
            tier="cloud",
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def health(self) -> AdapterHealth:
        # Deliberately no network probe: a health check must not spend money or leak traffic.
        return AdapterHealth(
            provider=self.provider,
            model=self.model,
            tier="cloud",
            configured=True,
            reachable=None,
            detail="configured; not probed",
        )
