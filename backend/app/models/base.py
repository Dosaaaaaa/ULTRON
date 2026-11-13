"""Provider-independent model adapter interface. Cloud and local adapters stay separate."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant"]
Tier = Literal["cloud", "local"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class ModelRequest:
    messages: list[ChatMessage]
    max_output_tokens: int
    temperature: float


@dataclass(frozen=True)
class ModelResponse:
    content: str
    provider: str
    model: str
    tier: Tier
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class StreamChunk:
    """A piece of visible answer text. The final item of a stream is the ModelResponse."""

    text: str


@dataclass(frozen=True)
class AdapterHealth:
    provider: str
    model: str
    tier: Tier
    configured: bool
    reachable: bool | None  # None = not probed (cloud is never probed: it would cost money)
    detail: str


class ModelError(Exception):
    """Base for adapter failures. `kind` is a stable, log-safe label (never contains secrets)."""

    kind = "model_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.kind)


class ProviderTimeout(ModelError):
    kind = "timeout"


class ProviderUnavailable(ModelError):
    """Connection refused, DNS failure, 429 or 5xx: worth falling back from."""

    kind = "unavailable"


class ProviderRateLimited(ProviderUnavailable):
    """HTTP 429. Carries what the provider said so a budget can correct itself from it."""

    kind = "rate_limited"

    def __init__(
        self, message: str = "", *, body: str = "", retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.body = body  # quota numbers only are parsed from it; it is never logged
        self.retry_after_s = retry_after_s


class ProviderRejected(ModelError):
    """4xx other than 429: bad credentials, bad model name, bad request."""

    kind = "rejected"


class ProviderBadResponse(ModelError):
    kind = "bad_response"


class ModelAdapter(ABC):
    provider: str
    model: str
    tier: Tier
    timeout_s: float

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk | ModelResponse]:
        """Yield StreamChunk items, then one final ModelResponse (full text, usage, latency).

        Default: no real streaming; the whole answer arrives as a single chunk.
        """
        response = await self.complete(request)
        yield StreamChunk(response.content)
        yield response

    @abstractmethod
    async def health(self) -> AdapterHealth: ...
