"""One streamed chat turn over a WebSocket, with cancellation (barge-in) support.

Server -> client events:
  start  {engine, fallback, degraded, notice, privacy_locked, memory}   once, before the first delta
  delta  {text}                                                 zero or more
  end    {conversation_id, message_id, reply, interrupted, usage, privacy_locked, notice}
  error  {code, message, conversation_id?, partial?, attempts?}
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.errors import AppError
from app.core.orchestrator import (
    ESCALATION_NOTICE,
    ChatResult,
    lock_for,
    persist,
    prepare,
    prompt_builder,
    run_reasoning,
    undo_remember,
)
from app.core.services import Services
from app.models.base import StreamChunk
from app.models.router import (
    AllModelsFailed,
    Privacy,
    RoutedResponse,
    RouteInfo,
    StreamFailed,
    StreamStarted,
    classify_complexity,
)

log = logging.getLogger("ultron.stream")
Send = Callable[[dict], Awaitable[None]]


@dataclass
class TurnState:
    """Shared with the socket reader so a cancel can say what was actually spoken."""

    spoken: str | None = None
    partial: list[str] = field(default_factory=list)
    started: StreamStarted | None = None


def _notice(route: RouteInfo | None, escalated: bool) -> str | None:
    parts = [route.notice if route else None, ESCALATION_NOTICE if escalated else None]
    return " ".join(p for p in parts if p) or None


async def run_turn(
    services: Services,
    send: Send,
    state: TurnState,
    *,
    text: str,
    conversation_id: str | None,
    privacy: Privacy,
    voice: bool,
    project_id: str | None = None,
) -> None:
    """Run one turn. Cancel this task to interrupt; it persists what was said and ends cleanly."""
    try:
        if conversation_id is None:
            await _turn(services, send, state, text, None, privacy, voice, project_id)
        else:
            async with lock_for(conversation_id):
                await _turn(
                    services, send, state, text, conversation_id, privacy, voice, project_id
                )
    except AppError as exc:
        await send({"type": "error", "code": exc.code, "message": exc.message, **exc.extra})


async def _turn(
    services: Services,
    send: Send,
    state: TurnState,
    text: str,
    conversation_id: str | None,
    privacy: Privacy,
    voice: bool,
    project_id: str | None,
) -> None:
    try:
        prepared = await prepare(services, text, conversation_id, privacy, voice, project_id)
    except asyncio.CancelledError:
        # Interrupted while memory/web were being gathered: nothing was said or saved, but the
        # client still gets its `end` so it never waits for one (prepare rolled back any note).
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()
        await send(
            {
                "type": "end",
                "interrupted": True,
                "conversation_id": conversation_id,
                "message_id": None,
                "reply": "",
                "usage": None,
                "privacy_locked": privacy == "local_only",
                "notice": None,
            }
        )
        return
    try:
        await run_reasoning(services, prepared, text)
    except asyncio.CancelledError:
        # Interrupted (barge-in, NEW) during the private solve, before anything was said: keep
        # nothing, and still answer the client with an `end` so it never waits for one.
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()
        await undo_remember(services, prepared)
        await send(
            {
                "type": "end",
                "interrupted": True,
                "conversation_id": conversation_id,
                "message_id": None,
                "reply": "",
                "usage": None,
                "privacy_locked": prepared.effective == "local_only",
                "notice": None,
            }
        )
        return
    stream = services.router.stream(
        prepared.history,
        build_system_prompt=prompt_builder(services, prepared),
        privacy=prepared.effective,
        complexity=classify_complexity(text),
        max_output_tokens=prepared.max_output_tokens,
        temperature=services.settings.temperature,
        voice=voice,
    )
    final: RoutedResponse | None = None
    try:
        async for item in stream:
            if isinstance(item, StreamStarted):
                state.started = item
                await send(
                    {
                        "type": "start",
                        "engine": {
                            "provider": item.provider,
                            "model": item.model,
                            "tier": item.tier,
                        },
                        "fallback": item.route.fallback,
                        "degraded": item.route.degraded,
                        "notice": _notice(item.route, prepared.escalated),
                        "privacy_locked": prepared.effective == "local_only",
                        "memory": prepared.memory.report(item.tier),
                        "web": prepared.web.report(),
                        "reasoning": prepared.reasoning,
                        "actions": prepared.actions,
                    }
                )
            elif isinstance(item, StreamChunk):
                state.partial.append(item.text)
                await send({"type": "delta", "text": item.text})
            else:
                final = item
    except AllModelsFailed as exc:
        await undo_remember(services, prepared)  # nothing saved, so no memory either
        r = exc.route
        await send(
            {
                "type": "error",
                "code": "models_unavailable",
                "message": "No model could answer this request. Nothing was saved.",
                "reason": r.reason,
                "attempts": [
                    {
                        "provider": a.provider,
                        "model": a.model,
                        "tier": a.tier,
                        "error": a.error_kind,
                    }
                    for a in r.attempts
                ],
            }
        )
        return
    except StreamFailed as exc:
        saved = await _persist_partial(
            services,
            prepared,
            text,
            conversation_id,
            "".join(state.partial),
            exc.route,
            state,
            {"error": exc.kind},
        )
        if saved is None:
            await undo_remember(services, prepared)
        await send(
            {
                "type": "error",
                "code": "stream_failed",
                "message": (
                    f"The model failed mid-answer ({exc.kind}). The partial reply was saved."
                ),
                "partial": True,
                "conversation_id": saved.conversation_id if saved else None,
            }
        )
        return
    except asyncio.CancelledError:
        # Barge-in / client cancel. Keep going long enough to persist what the operator heard.
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()
        content = state.spoken if state.spoken is not None else "".join(state.partial)
        saved = await _persist_partial(
            services,
            prepared,
            text,
            conversation_id,
            content,
            _route_of(stream, state),
            state,
            {"interrupted": True},
        )
        if saved is None:  # nothing was spoken, so nothing (not even a memory) is kept
            await undo_remember(services, prepared)
        await send(
            {
                "type": "end",
                "interrupted": True,
                "conversation_id": saved.conversation_id if saved else conversation_id,
                "message_id": saved.message.id if saved else None,
                "reply": content,
                "usage": None,
                "privacy_locked": prepared.effective == "local_only",
                "notice": None,
            }
        )
        return
    finally:
        await stream.aclose()

    assert final is not None
    r = final.response
    result = await persist(
        services,
        prepared,
        text=text,
        conversation_id=conversation_id,
        content=r.content,
        provider=r.provider,
        model=r.model,
        tier=r.tier,
        route=final.route,
        response=r,
    )
    await send(_end_event(result, r))


def _end_event(result: ChatResult, r) -> dict:
    return {
        "type": "end",
        "interrupted": False,
        "conversation_id": result.conversation_id,
        "message_id": result.message.id,
        "reply": result.message.content,
        "usage": {
            "latency_ms": r.latency_ms,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
        },
        "privacy_locked": result.privacy_locked,
        "notice": _notice(result.route, result.privacy_escalated),
        "memory": result.memory,
        "web": result.web,
        "reasoning": result.reasoning,
    }


def _route_of(_stream, state: TurnState) -> RouteInfo:
    if state.started is not None:
        return state.started.route
    return RouteInfo("n/a", "n/a", "n/a", "interrupted before the first token")


async def _persist_partial(
    services, prepared, text, conversation_id, content, route, state, extra
) -> ChatResult | None:
    """Save the user turn plus whatever reply text existed. Nothing spoken => nothing saved."""
    if not content.strip():
        return None
    st = state.started
    return await persist(
        services,
        prepared,
        text=text,
        conversation_id=conversation_id,
        content=content.strip(),
        provider=st.provider if st else "unknown",
        model=st.model if st else "unknown",
        tier=st.tier if st else "unknown",
        route=route,
        response=None,
        route_extra=extra,
    )
