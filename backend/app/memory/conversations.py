"""Conversation persistence (working/episodic memory, Phase 1 scope: plain message history)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models import Conversation, Message, utcnow


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    title: str | None
    created_at: object
    updated_at: object
    message_count: int
    local_only: bool


async def get_conversation(session: AsyncSession, conversation_id: str) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFound("Conversation not found.")
    return conv


async def recent_messages(session: AsyncSession, conversation_id: str, limit: int) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    return list(reversed(list(rows)))


async def all_messages(session: AsyncSession, conversation_id: str) -> list[Message]:
    rows = await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    return list(rows)


async def list_conversations(
    session: AsyncSession, *, limit: int, offset: int
) -> list[ConversationSummary]:
    counts = (
        select(Message.conversation_id, func.count(Message.id).label("n"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = await session.execute(
        select(Conversation, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        ConversationSummary(c.id, c.title, c.created_at, c.updated_at, n, c.local_only)
        for c, n in rows.all()
    ]


async def amend_spoken(session: AsyncSession, message_id: int, spoken: str) -> Message | None:
    """Replace an assistant reply with the part the operator actually heard.

    Used when a barge-in arrives after the model had already finished. Nothing heard => the
    assistant message is removed (the user's message stays). Returns None when removed.
    """
    msg = await session.get(Message, message_id)
    if msg is None or msg.role != "assistant":
        raise NotFound("Assistant message not found.")
    spoken = spoken.strip()
    if not spoken:
        await session.delete(msg)
        await session.commit()
        return None
    msg.route = {
        **(msg.route or {}),
        "interrupted": True,
        "amended": True,
        "generated_chars": len(msg.content),
    }
    msg.content = spoken
    await session.commit()
    return msg


async def delete_conversation(session: AsyncSession, conversation_id: str) -> None:
    await get_conversation(session, conversation_id)
    await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await session.commit()


async def append_exchange(
    session: AsyncSession,
    *,
    conversation_id: str | None,
    user_text: str,
    assistant: Message,
    lock_local_only: bool = False,
) -> tuple[Conversation, Message]:
    """Persist the user message and the assistant reply in one transaction."""
    now = utcnow()
    if conversation_id is None:
        conv = Conversation(
            title=user_text[:60], created_at=now, updated_at=now, local_only=lock_local_only
        )
        session.add(conv)
        await session.flush()
    else:
        conv = await get_conversation(session, conversation_id)
    session.add(Message(conversation_id=conv.id, role="user", content=user_text, created_at=now))
    await session.flush()  # user message gets the lower id
    assistant.conversation_id = conv.id
    assistant.created_at = utcnow()
    session.add(assistant)
    conv.updated_at = assistant.created_at
    if lock_local_only:
        conv.local_only = True  # one-way: there is deliberately no API to unlock
    await session.commit()
    return conv, assistant
