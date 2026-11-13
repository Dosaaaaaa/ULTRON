"""ORM tables. Portable across SQLite (default) and PostgreSQL (+ pgvector for memory)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UtcDateTime(TypeDecorator):
    """Always timezone-aware UTC, even on SQLite which drops tzinfo."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(UTC) if value else None

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    # Sticky privacy: once any turn was local_only, nothing in this conversation may reach a cloud
    # provider again (the history window would otherwise carry the private turn along).
    local_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    # Provenance of assistant messages (null for user messages)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    tier: Mapped[str | None] = mapped_column(String(16))
    personality_version: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    route: Mapped[dict | None] = mapped_column(JSON)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class EmbeddingType(TypeDecorator):
    """A float vector: a pgvector `vector` on PostgreSQL, JSON text on SQLite (searched in numpy).

    The PostgreSQL column has no fixed dimension, so changing the embedding model never needs a
    migration; rows record which model made them and are only ever compared with the same model.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        values = [float(x) for x in value]
        return values if dialect.name == "postgresql" else json.dumps(values)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value] if dialect.name == "postgresql" else json.loads(value)


# Memory kinds (spec section 5). Working memory is the conversation window, not a stored kind.
MEMORY_KINDS = ("semantic", "episodic", "project")
# confirmed = the operator said or approved it; proposed = a model suggested it and it is NOT used
# as a fact until the operator confirms it.
MEMORY_STATUSES = ("confirmed", "proposed")
MEMORY_SOURCES = ("user", "extracted", "api")
MEMORY_PRIVACY = ("normal", "local_only")  # local_only: never placed in a cloud model's prompt
PROJECT_STATUSES = ("active", "paused", "done")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    memories: Mapped[list[Memory]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", index=True)
    source: Mapped[str] = mapped_column(String(16), default="user")
    privacy: Mapped[str] = mapped_column(String(16), default="normal")
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Which conversation it came from. SET NULL: deleting a conversation keeps what the operator
    # chose to remember, but drops the link.
    source_conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, index=True)

    project: Mapped[Project | None] = relationship(back_populates="memories")


class MemoryEvent(Base):
    """Audit trail for memory changes. Never holds memory text, only ids and counts."""

    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(24))
    memory_id: Mapped[str | None] = mapped_column(String(36))  # no FK: outlives the row
    actor: Mapped[str] = mapped_column(String(24))
    detail: Mapped[dict | None] = mapped_column(JSON)


# ----------------------------------------------------------------------------- devices (phase 5)
DEVICE_CAPS = ("chat", "notify", "call", "reminders")  # what a paired phone may be allowed to do
DEVICE_KINDS = ("phone", "tablet", "desktop", "other")


class Device(Base):
    """A paired client (the phone). It holds its own token; only the token's hash is stored here."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class OutboxItem(Base):
    """Something to deliver to a device; kept until it acknowledges, so nothing is missed."""

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # notify | reminder | call_proposal
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # acknowledged by the device


REMINDER_STATUSES = ("pending", "fired", "cancelled")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    text: Mapped[str] = mapped_column(String(300))
    due_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    fired_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Preference(Base):
    """A few owner-level switches (for example: ask before dialling). One row per key."""

    __tablename__ = "preferences"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
