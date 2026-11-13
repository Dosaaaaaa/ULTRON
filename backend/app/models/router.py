"""Model router: picks cloud or local per request, falls back honestly, tracks usage.

Privacy invariant: when a request is local-only (per-request flag, `local_only` policy, or the
global allow_cloud=False switch) the cloud adapter is never in the candidate list, so no failure
path can send that request to a cloud provider.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from app.core.style import StyleGuard, apply_style
from app.models.base import (
    ChatMessage,
    ModelAdapter,
    ModelError,
    ModelRequest,
    ModelResponse,
    ProviderTimeout,
    StreamChunk,
)

log = logging.getLogger("ultron.router")

Privacy = Literal["default", "local_only"]
Complexity = Literal["simple", "complex"]
Policy = Literal["auto", "cloud_first", "local_first", "local_only"]

_COMPLEX_HINTS = re.compile(
    r"\b(analy[sz]e|strateg\w*|plan|design|architect\w*|compare|trade-?offs?|why|explain|debug|"
    r"code|implement|prove|evaluate|research|critique|decide|should i|pros and cons|optimi[sz]e)\b",
    re.IGNORECASE,
)


def classify_complexity(text: str) -> Complexity:
    """Cheap heuristic. It decides which engine is tried first, nothing more.

    It is not an intent classifier; a model-based one is a later improvement.
    """
    if len(text) > 400 or text.count("?") > 1 or _COMPLEX_HINTS.search(text):
        return "complex"
    return "simple"


@dataclass(frozen=True)
class PromptContext:
    """What the prompt builder needs to know about the engine that will answer."""

    tier: str
    provider: str
    model: str
    degraded: bool
    local_only: bool
    voice: bool = False  # the reply will be spoken aloud


PromptBuilder = Callable[[PromptContext], str]


@dataclass
class Attempt:
    provider: str
    model: str
    tier: str
    ok: bool
    error_kind: str | None = None
    latency_ms: int | None = None


@dataclass
class RouteInfo:
    policy: str
    privacy: str
    complexity: str
    reason: str
    attempts: list[Attempt] = field(default_factory=list)
    fallback: bool = False
    degraded: bool = False
    notice: str | None = None


@dataclass
class RoutedResponse:
    response: ModelResponse
    route: RouteInfo


@dataclass
class StreamStarted:
    """Emitted once, just before the first text chunk: which engine is answering and why."""

    provider: str
    model: str
    tier: str
    route: RouteInfo


class StreamFailed(Exception):
    """The engine failed after text had already been sent; fallback is not possible."""

    def __init__(self, partial: str, route: RouteInfo, kind: str) -> None:
        super().__init__("stream failed after output started")
        self.partial = partial
        self.route = route
        self.kind = kind


class AllModelsFailed(Exception):
    def __init__(self, route: RouteInfo) -> None:
        super().__init__("no model could answer")
        self.route = route


@dataclass
class _Stats:
    requests: int = 0
    failures: int = 0
    total_latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ModelRouter:
    def __init__(
        self,
        *,
        cloud: ModelAdapter | None,
        local: ModelAdapter | None,
        policy: Policy = "auto",
        allow_cloud: bool = True,
        enforce_style: bool = True,
    ) -> None:
        self._enforce_style = enforce_style
        self._cloud = cloud
        self._local = local
        self._policy = policy
        self._allow_cloud = allow_cloud
        self._stats: dict[str, _Stats] = {}

    @property
    def local_adapter(self) -> ModelAdapter | None:
        """The on-device engine. For jobs that must never use the cloud (memory extraction)."""
        return self._local

    # -- planning -------------------------------------------------------
    def plan(self, privacy: Privacy, complexity: Complexity) -> tuple[list[ModelAdapter], str]:
        local_only = (
            privacy == "local_only" or self._policy == "local_only" or not self._allow_cloud
        )
        local = [self._local] if self._local else []
        cloud = [self._cloud] if (self._cloud and not local_only) else []

        if local_only:
            why = (
                "request marked local_only"
                if privacy == "local_only"
                else "cloud disabled by configuration"
            )
            return local, why
        if self._policy == "cloud_first":
            return cloud + local, "policy cloud_first"
        if self._policy == "local_first":
            return local + cloud, "policy local_first"
        if complexity == "complex":
            return cloud + local, "auto: complex request prefers cloud"
        return local + cloud, "auto: simple request prefers local"

    # -- execution ------------------------------------------------------
    async def complete(
        self,
        history: list[ChatMessage],
        *,
        build_system_prompt: PromptBuilder,
        privacy: Privacy = "default",
        complexity: Complexity = "simple",
        max_output_tokens: int = 1024,
        temperature: float = 0.6,
        voice: bool = False,
        style: bool = True,
    ) -> RoutedResponse:
        candidates, reason = self.plan(privacy, complexity)
        local_only = privacy == "local_only" or not any(c.tier == "cloud" for c in candidates)
        route = RouteInfo(self._policy, privacy, complexity, reason)
        preferred_tier = candidates[0].tier if candidates else None

        for index, adapter in enumerate(candidates):
            fell_back = index > 0
            degraded = adapter.tier == "local" and (
                (fell_back and preferred_tier == "cloud") or complexity == "complex"
            )
            system = build_system_prompt(
                PromptContext(
                    adapter.tier, adapter.provider, adapter.model, degraded, local_only, voice
                )
            )
            request = ModelRequest(
                [ChatMessage("system", system), *history], max_output_tokens, temperature
            )
            try:
                # Outer deadline is a backstop; adapters already set their own HTTP timeout.
                async with asyncio.timeout(adapter.timeout_s + 2):
                    response = await adapter.complete(request)
                # style=False is for private working that is never shown or spoken (the reasoning
                # pass): the no-'!' rule would corrupt factorials and code literals in it.
                if self._enforce_style and style:
                    response = replace(response, content=apply_style(response.content) or "No.")
            except (ModelError, TimeoutError) as exc:
                kind = exc.kind if isinstance(exc, ModelError) else ProviderTimeout.kind
                route.attempts.append(
                    Attempt(adapter.provider, adapter.model, adapter.tier, False, kind)
                )
                self._record_failure(adapter)
                log.warning(
                    "model_attempt_failed",
                    extra={"provider": adapter.provider, "model": adapter.model, "kind": kind},
                )
                continue

            route.attempts.append(
                Attempt(
                    adapter.provider, adapter.model, adapter.tier, True, None, response.latency_ms
                )
            )
            route.fallback = fell_back
            route.degraded = degraded
            route.notice = self._notice(route, adapter, fell_back, degraded)
            self._record_success(response)
            return RoutedResponse(response, route)

        if not candidates:
            route.reason += "; no eligible model is configured"
        raise AllModelsFailed(route)

    async def stream(
        self,
        history: list[ChatMessage],
        *,
        build_system_prompt: PromptBuilder,
        privacy: Privacy = "default",
        complexity: Complexity = "simple",
        max_output_tokens: int = 1024,
        temperature: float = 0.6,
        voice: bool = False,
    ) -> AsyncIterator[StreamStarted | StreamChunk | RoutedResponse]:
        """Like complete(), but yields StreamStarted, StreamChunk..., then RoutedResponse.

        Fallback to the next engine is only possible before the first chunk is emitted. After
        that a failure raises StreamFailed carrying the partial text; the caller decides what to
        keep. Closing this generator closes the upstream HTTP stream, which stops generation.
        """
        candidates, reason = self.plan(privacy, complexity)
        local_only = privacy == "local_only" or not any(c.tier == "cloud" for c in candidates)
        route = RouteInfo(self._policy, privacy, complexity, reason)
        preferred_tier = candidates[0].tier if candidates else None

        for index, adapter in enumerate(candidates):
            fell_back = index > 0
            degraded = adapter.tier == "local" and (
                (fell_back and preferred_tier == "cloud") or complexity == "complex"
            )
            system = build_system_prompt(
                PromptContext(
                    adapter.tier, adapter.provider, adapter.model, degraded, local_only, voice
                )
            )
            request = ModelRequest(
                [ChatMessage("system", system), *history], max_output_tokens, temperature
            )
            events = adapter.stream(request)
            partial: list[str] = []
            guard = StyleGuard() if self._enforce_style else None

            async def emit(
                text: str,
                *,
                adapter=adapter,
                fell_back=fell_back,
                degraded=degraded,
                partial=partial,
            ):
                """Yield StreamStarted (first visible text only) then the chunk."""
                if not text:
                    return
                if not partial:
                    route.fallback, route.degraded = fell_back, degraded
                    route.notice = self._notice(route, adapter, fell_back, degraded)
                    yield StreamStarted(adapter.provider, adapter.model, adapter.tier, route)
                partial.append(text)
                yield StreamChunk(text)

            try:
                while True:
                    try:
                        item = await asyncio.wait_for(events.__anext__(), adapter.timeout_s + 2)
                    except StopAsyncIteration:
                        break
                    if isinstance(item, StreamChunk):
                        async for ev in emit(guard.feed(item.text) if guard else item.text):
                            yield ev
                    else:  # final ModelResponse
                        if guard:
                            async for ev in emit(guard.flush()):
                                yield ev
                            if not partial:  # the model produced only emoji / '!'
                                async for ev in emit("No."):
                                    yield ev
                            item = replace(item, content="".join(partial).strip())
                        route.attempts.append(
                            Attempt(
                                adapter.provider,
                                adapter.model,
                                adapter.tier,
                                True,
                                None,
                                item.latency_ms,
                            )
                        )
                        self._record_success(item)
                        yield RoutedResponse(item, route)
                        return
            except (ModelError, TimeoutError) as exc:
                kind = exc.kind if isinstance(exc, ModelError) else ProviderTimeout.kind
                route.attempts.append(
                    Attempt(adapter.provider, adapter.model, adapter.tier, False, kind)
                )
                self._record_failure(adapter)
                log.warning(
                    "model_stream_failed",
                    extra={
                        "provider": adapter.provider,
                        "model": adapter.model,
                        "kind": kind,
                        "after_output": bool(partial),
                    },
                )
                if partial:
                    raise StreamFailed("".join(partial), route, kind) from exc
                continue
            finally:
                await events.aclose()

        if not candidates:
            route.reason += "; no eligible model is configured"
        raise AllModelsFailed(route)

    @staticmethod
    def _notice(
        route: RouteInfo, served: ModelAdapter, fell_back: bool, degraded: bool
    ) -> str | None:
        if fell_back and degraded:
            failed = route.attempts[0]
            return (
                f"Preferred engine {failed.provider}/{failed.model} failed ({failed.error_kind}). "
                f"Answered by local model {served.model}, which is less capable on complex "
                "reasoning. Verify anything important."
            )
        if fell_back:
            failed = route.attempts[0]
            return (
                f"Local engine {failed.provider}/{failed.model} failed ({failed.error_kind}); "
                f"answered by {served.provider}/{served.model}."
            )
        if degraded:
            return (
                f"Answered by local model {served.model} ({route.reason}). It is less capable "
                "than cloud models on complex reasoning."
            )
        return None

    # -- metrics --------------------------------------------------------
    def _bucket(self, adapter_key: str) -> _Stats:
        return self._stats.setdefault(adapter_key, _Stats())

    def _record_failure(self, adapter: ModelAdapter) -> None:
        s = self._bucket(f"{adapter.provider}/{adapter.model}")
        s.requests += 1
        s.failures += 1

    def _record_success(self, r: ModelResponse) -> None:
        s = self._bucket(f"{r.provider}/{r.model}")
        s.requests += 1
        s.total_latency_ms += r.latency_ms
        s.prompt_tokens += r.prompt_tokens or 0
        s.completion_tokens += r.completion_tokens or 0

    def usage(self) -> dict[str, dict[str, float | int]]:
        out: dict[str, dict[str, float | int]] = {}
        for key, s in self._stats.items():
            ok = s.requests - s.failures
            out[key] = {
                "requests": s.requests,
                "failures": s.failures,
                "avg_latency_ms": round(s.total_latency_ms / ok) if ok else 0,
                "prompt_tokens": s.prompt_tokens,
                "completion_tokens": s.completion_tokens,
            }
        return out

    @property
    def adapters(self) -> list[ModelAdapter]:
        return [a for a in (self._cloud, self._local) if a]
