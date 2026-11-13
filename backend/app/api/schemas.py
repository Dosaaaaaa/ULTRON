from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_CHARS = 8000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    conversation_id: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F-]{36}$", description="Omit to start a conversation."
    )
    privacy: Literal["default", "local_only"] = "default"
    modality: Literal["text", "voice"] = "text"  # voice => short plain-spoken reply
    project_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F-]{36}$",
        description="Scope memory retrieval to this project (plus memories not tied to one).",
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v


class AttemptOut(BaseModel):
    provider: str
    model: str
    tier: str
    ok: bool
    error_kind: str | None
    latency_ms: int | None


class RouteOut(BaseModel):
    policy: str
    privacy: str
    complexity: str
    reason: str
    fallback: bool
    degraded: bool
    attempts: list[AttemptOut]


class EngineOut(BaseModel):
    provider: str
    model: str
    tier: str


class UsageOut(BaseModel):
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None


class MemoryRefOut(BaseModel):
    id: str
    kind: str
    content: str


class RememberedOut(BaseModel):
    saved: bool
    memory_id: str | None
    reason: str | None


class MemoryReportOut(BaseModel):
    enabled: bool
    used: list[MemoryRefOut]  # the memories that reached the answering engine
    note: str | None
    remembered: RememberedOut | None


class WebSourceOut(BaseModel):
    title: str
    url: str


class WebReportOut(BaseModel):
    enabled: bool
    used: bool  # True only if results were retrieved and given to the model
    provider: str | None
    query: str | None  # what was sent to the search provider
    sources: list[WebSourceOut]
    withheld: int = 0  # results dropped as unsafe (orders aimed at the model, inside addresses)
    trimmed: int = 0  # results kept as title + link only
    note: str | None


class ActionOut(BaseModel):
    """Something ULTRON did on a command, instead of the model (a call proposal, a reminder)."""

    type: str  # call | reminder
    status: str  # call: awaiting_confirmation | no_phone; reminder: created | unclear
    request_id: str | None = None
    target: str | None = None
    devices: int | None = None
    reminder_id: str | None = None
    text: str | None = None
    due_at: str | None = None
    due_local: str | None = None
    why: str | None = None


class ReasoningReportOut(BaseModel):
    used: bool  # True if a private solving pass ran and its working informed the reply
    reason: str | None = None  # why not, when it did not run
    engine: str | None = None
    seconds: float | None = None
    answer: str | None = None
    working: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: int
    reply: str
    created_at: datetime
    engine: EngineOut
    route: RouteOut
    usage: UsageOut
    personality_version: int
    privacy_locked: bool = Field(
        description="True when the conversation is permanently local-only (a turn was local_only)."
    )
    notice: str | None = Field(
        description="Set when the answer came from a degraded/fallback engine."
    )
    memory: MemoryReportOut | None = None
    web: WebReportOut | None = None
    reasoning: ReasoningReportOut | None = None
    actions: list[ActionOut] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    engine: EngineOut | None = None
    personality_version: int | None = None
    route: dict | None = None


class ConversationOut(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    local_only: bool


class ConversationDetail(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    local_only: bool
    messages: list[MessageOut]
