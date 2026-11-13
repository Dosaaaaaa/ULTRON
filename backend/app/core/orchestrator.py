"""Chat orchestration shared by REST and WebSocket: history -> router -> persistence."""

from __future__ import annotations

import asyncio
import contextlib
import time
import weakref
from dataclasses import asdict, dataclass, field

from app.core.personality import WebPrompt
from app.core.reasoning import SOLVER_SYSTEM, Working, needs_reasoning, parse_working
from app.core.services import Services
from app.db.models import Message
from app.devices import reminders as reminder_service
from app.devices.calls import action_note, propose_call
from app.devices.remind_cmd import propose_reminder
from app.memory import conversations as repo
from app.memory.context import MemoryContext, gather, remembered_by_this_turn
from app.models.base import ChatMessage, ModelResponse
from app.models.router import (
    AllModelsFailed,
    Privacy,
    PromptBuilder,
    PromptContext,
    RouteInfo,
    classify_complexity,
)
from app.tools.web import WebContext, gather_web

# One in-flight exchange per conversation so concurrent requests cannot interleave history.
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def lock_for(conversation_id: str) -> asyncio.Lock:
    lock = _locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[conversation_id] = lock
    return lock


ESCALATION_NOTICE = (
    "This conversation is local-only because an earlier turn was marked local_only; "
    "this turn was processed on-device. Start a new conversation to use cloud models."
)


@dataclass
class ChatResult:
    conversation_id: str
    message: Message
    route: RouteInfo
    privacy_locked: bool  # conversation is local-only (now or from an earlier turn)
    privacy_escalated: bool  # this request asked for default privacy but was forced local
    memory: dict | None = None  # what long-term memory did this turn (see MemoryContext.report)
    web: dict | None = None  # what web search did this turn (see WebContext.report)
    reasoning: dict | None = None  # what the private reasoning pass did this turn
    actions: list[dict] = field(default_factory=list)  # e.g. a call proposal sent to the phone


@dataclass
class Prepared:
    history: list[ChatMessage]  # includes the new user message
    locked: bool
    escalated: bool
    effective: Privacy
    voice: bool
    max_output_tokens: int
    memory: MemoryContext
    web: WebContext
    working: Working | None = None
    reasoning: dict | None = None  # report: what the private reasoning pass did this turn
    actions: list[dict] = field(default_factory=list)


def trim_history(history: list[ChatMessage], budget_chars: int) -> list[ChatMessage]:
    """Keep the newest turns that fit `budget_chars` (always at least the last exchange) and never
    start the window on an assistant reply. Long chats then stop costing more tokens per turn."""
    kept: list[ChatMessage] = []
    used = 0
    for m in reversed(history):
        if kept and len(kept) >= 2 and used + len(m.content) > budget_chars:
            break
        kept.append(m)
        used += len(m.content)
    kept.reverse()
    while len(kept) > 1 and kept[0].role == "assistant":
        kept.pop(0)
    return kept


async def prepare(
    services: Services,
    text: str,
    conversation_id: str | None,
    privacy: Privacy,
    voice: bool,
    project_id: str | None = None,
) -> Prepared:
    settings = services.settings
    history: list[ChatMessage] = []
    locked = False
    if conversation_id is not None:
        async with services.sessionmaker() as session:
            conv = await repo.get_conversation(session, conversation_id)  # 404 early
            locked = conv.local_only
            rows = await repo.recent_messages(
                session, conversation_id, settings.max_context_messages
            )
            history = trim_history(
                [ChatMessage(m.role, m.content) for m in rows],  # type: ignore[arg-type]
                settings.history_char_budget,
            )
    previous_user = next((m.content for m in reversed(history) if m.role == "user"), None)
    effective: Privacy = "local_only" if (locked or privacy == "local_only") else "default"
    memory = await gather(
        services,
        text=text,
        previous_user_text=previous_user,
        project_id=project_id,
        local_only=effective == "local_only",
        conversation_id=conversation_id,
    )
    try:
        web = await gather_web(
            services.web,
            services.web_cap,
            text=text,
            local_only=effective == "local_only",
            auto=settings.web_search_auto,
            count=settings.web_search_count,
        )
    except asyncio.CancelledError:
        # Interrupted before anything was said: a note this turn's command stored must not stay.
        await _rollback_remembered(services, memory)
        raise
    history.append(ChatMessage("user", text))
    actions = [
        a for a in (await propose_call(services, text), await propose_reminder(services, text)) if a
    ]
    return Prepared(
        history=history,
        locked=locked,
        escalated=locked and privacy != "local_only",
        effective=effective,
        voice=voice,
        max_output_tokens=settings.voice_max_output_tokens if voice else settings.max_output_tokens,
        memory=memory,
        web=web,
        actions=actions,
    )


async def run_reasoning(services: Services, prepared: Prepared, text: str) -> None:
    """Solve problem-like messages privately first. Never raises for model trouble: the reply is
    then simply written without the working, and the report says so."""
    s = services.settings
    if s.reasoning != "auto" or not needs_reasoning(text):
        why = "not needed" if s.reasoning == "auto" else "off"
        prepared.reasoning = {"used": False, "reason": why}
        return
    if prepared.voice and not s.reasoning_voice:
        prepared.reasoning = {"used": False, "reason": "disabled for voice"}
        return
    started = time.perf_counter()
    try:
        routed = await services.router.complete(
            prepared.history[-7:],
            build_system_prompt=lambda ctx: SOLVER_SYSTEM,
            privacy=prepared.effective,
            # "simple" = local first under the auto policy. Measured (docs/INTELLIGENCE.md): the
            # local model solving matched or beat the cloud model, and spends no cloud tokens.
            complexity="simple",
            max_output_tokens=s.reasoning_max_tokens,
            temperature=0.2,
            voice=False,
            style=False,  # private working: never shown or spoken, so not style-rewritten
        )
    except AllModelsFailed:
        prepared.reasoning = {"used": False, "reason": "no model could run the reasoning pass"}
        return
    working = parse_working(routed.response.content, routed.response.tier)
    seconds = round(time.perf_counter() - started, 1)
    if working is None:
        prepared.reasoning = {
            "used": False,
            "reason": "the solver returned nothing",
            "seconds": seconds,
        }
        return
    prepared.working = working
    prepared.reasoning = {
        "used": True,
        "engine": routed.response.tier,
        "seconds": seconds,
        "answer": working.answer,
        "working": working.text,
    }


async def undo_remember(services: Services, prepared: Prepared) -> None:
    """A turn that ends without saving anything must not leave a memory behind: the operator is
    told "nothing was saved" in that case, and that has to stay true."""
    await _rollback_remembered(services, prepared.memory)
    await _rollback_actions(services, prepared.actions)


async def _rollback_actions(services: Services, actions: list[dict]) -> None:
    """A reminder made by this turn goes too when the turn saved nothing ("Nothing was saved")."""
    for a in actions:
        if a.get("type") == "reminder" and a.get("status") == "created":
            with contextlib.suppress(Exception):
                await reminder_service.cancel(services, a["reminder_id"])


async def _rollback_remembered(services: Services, memory: MemoryContext) -> None:
    made = remembered_by_this_turn(memory)
    if made and services.memory is not None:
        with contextlib.suppress(Exception):
            await services.memory.delete(made, actor="system", action="rollback")


def prompt_builder(services: Services, prepared: Prepared) -> PromptBuilder:
    """System prompt per candidate engine: the memory block is filtered for that engine's tier."""

    def build(ctx: PromptContext) -> str:
        w = prepared.web
        return services.personality.system_prompt(
            ctx,
            memory=prepared.memory.prompt_for(ctx),
            web=WebPrompt(enabled=w.enabled, block=w.block(), note=w.note),
            working=prepared.working.note() if prepared.working else None,
            action_note=action_note(prepared.actions),
        )

    return build


async def persist(
    services: Services,
    prepared: Prepared,
    *,
    text: str,
    conversation_id: str | None,
    content: str,
    provider: str,
    model: str,
    tier: str,
    route: RouteInfo,
    response: ModelResponse | None,
    route_extra: dict | None = None,
) -> ChatResult:
    """Save user + assistant message in one transaction (also sets the one-way privacy lock)."""
    memory = prepared.memory.report(tier)
    web = prepared.web.report()
    assistant = Message(
        role="assistant",
        content=content,
        provider=provider,
        model=model,
        tier=tier,
        personality_version=services.personality.personality.version,
        latency_ms=response.latency_ms if response else None,
        prompt_tokens=response.prompt_tokens if response else None,
        completion_tokens=response.completion_tokens if response else None,
        # ids only: the audit record of which memories shaped this reply holds no memory text
        route={
            **asdict(route),
            **(route_extra or {}),
            "memory": {**memory, "used": [m["id"] for m in memory["used"]]},
            "web": web,
            # the audit keeps that it ran and what it concluded, not the working text itself
            "reasoning": {k: v for k, v in (prepared.reasoning or {}).items() if k != "working"},
            "actions": prepared.actions,
        },
    )
    async with services.sessionmaker() as session:
        conv, assistant = await repo.append_exchange(
            session,
            conversation_id=conversation_id,
            user_text=text,
            assistant=assistant,
            lock_local_only=prepared.effective == "local_only",
        )
    if services.memory is not None:
        if memory["used"]:
            await services.memory.touch([m["id"] for m in memory["used"]])  # usage stats
        made = remembered_by_this_turn(prepared.memory)
        if made:
            await services.memory.attach_to_conversation(made, conv.id)
    return ChatResult(
        conv.id,
        assistant,
        route,
        conv.local_only,
        prepared.escalated,
        memory,
        web,
        prepared.reasoning,
        prepared.actions,
    )


async def handle_chat(
    services: Services,
    *,
    text: str,
    conversation_id: str | None,
    privacy: Privacy,
    voice: bool = False,
    project_id: str | None = None,
) -> ChatResult:
    if conversation_id is None:
        return await _exchange(services, text, None, privacy, voice, project_id)
    async with lock_for(conversation_id):
        return await _exchange(services, text, conversation_id, privacy, voice, project_id)


async def _exchange(
    services: Services,
    text: str,
    conversation_id: str | None,
    privacy: Privacy,
    voice: bool,
    project_id: str | None,
) -> ChatResult:
    prepared = await prepare(services, text, conversation_id, privacy, voice, project_id)
    # No DB transaction is held open across the (slow) model call.
    try:
        await run_reasoning(services, prepared, text)
        routed = await services.router.complete(
            prepared.history,
            build_system_prompt=prompt_builder(services, prepared),
            privacy=prepared.effective,
            complexity=classify_complexity(text),
            max_output_tokens=prepared.max_output_tokens,
            temperature=services.settings.temperature,
            voice=voice,
        )
    except BaseException:
        await undo_remember(services, prepared)  # the turn saved nothing, so neither does this
        raise
    r = routed.response
    return await persist(
        services,
        prepared,
        text=text,
        conversation_id=conversation_id,
        content=r.content,
        provider=r.provider,
        model=r.model,
        tier=r.tier,
        route=routed.route,
        response=r,
    )
