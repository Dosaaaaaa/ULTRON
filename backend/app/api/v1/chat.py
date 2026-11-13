from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import Principal, ServicesDep, device_can
from app.api.schemas import (
    ActionOut,
    AttemptOut,
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationOut,
    EngineOut,
    MemoryReportOut,
    MessageOut,
    ReasoningReportOut,
    RouteOut,
    UsageOut,
    WebReportOut,
)
from app.core.errors import ModelsUnavailable
from app.core.orchestrator import ESCALATION_NOTICE, handle_chat
from app.devices.registry import Who
from app.memory import conversations as repo
from app.models.router import AllModelsFailed

router = APIRouter(tags=["conversation"])


def _join(*parts: str | None) -> str | None:
    text = " ".join(p for p in parts if p)
    return text or None


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest, services: ServicesDep, _: Annotated[Who, Depends(device_can("chat"))]
) -> ChatResponse:
    try:
        result = await handle_chat(
            services,
            text=body.message,
            conversation_id=body.conversation_id,
            privacy=body.privacy,
            voice=body.modality == "voice",
            project_id=body.project_id,
        )
    except AllModelsFailed as exc:
        r = exc.route
        raise ModelsUnavailable(
            "No model could answer this request. Nothing was saved.",
            extra={
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
            },
        ) from exc

    m, route = result.message, result.route
    return ChatResponse(
        conversation_id=result.conversation_id,
        message_id=m.id,
        reply=m.content,
        created_at=m.created_at,
        engine=EngineOut(provider=m.provider, model=m.model, tier=m.tier),  # type: ignore[arg-type]
        route=RouteOut(
            policy=route.policy,
            privacy=route.privacy,
            complexity=route.complexity,
            reason=route.reason,
            fallback=route.fallback,
            degraded=route.degraded,
            attempts=[AttemptOut(**vars(a)) for a in route.attempts],
        ),
        usage=UsageOut(
            latency_ms=m.latency_ms,
            prompt_tokens=m.prompt_tokens,
            completion_tokens=m.completion_tokens,
        ),
        personality_version=m.personality_version,  # type: ignore[arg-type]
        privacy_locked=result.privacy_locked,
        notice=_join(route.notice, ESCALATION_NOTICE if result.privacy_escalated else None),
        memory=MemoryReportOut(**result.memory) if result.memory else None,
        web=WebReportOut(**result.web) if result.web else None,
        reasoning=ReasoningReportOut(**result.reasoning) if result.reasoning else None,
        actions=[ActionOut(**a) for a in result.actions],
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    services: ServicesDep,
    _: Principal,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationOut]:
    async with services.sessionmaker() as session:
        rows = await repo.list_conversations(session, limit=limit, offset=offset)
    return [ConversationOut(**vars(r)) for r in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str, services: ServicesDep, _: Principal
) -> ConversationDetail:
    async with services.sessionmaker() as session:
        conv = await repo.get_conversation(session, conversation_id)
        msgs = await repo.all_messages(session, conversation_id)
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        local_only=conv.local_only,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                engine=EngineOut(provider=m.provider, model=m.model, tier=m.tier)
                if m.provider and m.model and m.tier
                else None,
                personality_version=m.personality_version,
                route=m.route,
            )
            for m in msgs
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str, services: ServicesDep, _: Principal
) -> Response:
    async with services.sessionmaker() as session:
        await repo.get_conversation(session, conversation_id)  # 404 before anything is touched
    if services.memory is not None:  # suggestions made from this chat go with it
        await services.memory.discard_proposals_from(conversation_id)
    async with services.sessionmaker() as session:
        await repo.delete_conversation(session, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
