"""Suggest memories from a conversation. The operator decides what is kept.

Runs on the LOCAL model only (the cloud adapter is not reachable from here), so the transcript is
never sent anywhere new, and no cloud tokens are spent. Results are saved as `proposed` memories
(source "extracted"): they are NOT used in any prompt until the operator confirms them. Anything
that looks like a secret, is malformed, or repeats an existing memory is dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.core.errors import ModelsUnavailable
from app.core.services import Services
from app.db.models import MEMORY_KINDS, Memory
from app.memory import conversations as repo
from app.memory.store import InvalidMemory, SecretRejected
from app.models.base import ChatMessage, ModelError, ModelRequest

MAX_TRANSCRIPT_CHARS = 12000

INSTRUCTION = """You extract long-term notes from a conversation between an operator and an AI
assistant.

Write notes ONLY about things the OPERATOR said about themselves, their preferences, their people,
their plans, or their projects, that would still matter in a future conversation. Ignore small
talk, questions, hypotheticals, jokes, and anything only the assistant said. Never include
passwords, keys, tokens or card numbers. Each note is one short, self-contained sentence.

Kinds: "semantic" (a lasting fact or preference), "episodic" (something that happened),
"project" (work in progress, a goal or a decision).

Reply with ONLY a JSON array, no other text. Example:
[{"kind": "semantic", "text": "The operator prefers metric units."}]
If there is nothing worth keeping, reply with exactly: []"""


@dataclass
class Extraction:
    proposals: list[Memory] = field(default_factory=list)
    duplicates: int = 0
    dropped_secret: int = 0
    dropped_invalid: int = 0


def _transcript(messages: list) -> str:
    lines = [f"{'Operator' if m.role == 'user' else 'Assistant'}: {m.content}" for m in messages]
    text = "\n".join(lines)
    return text[-MAX_TRANSCRIPT_CHARS:]  # keep the most recent part


def parse_items(raw: str) -> list[dict]:
    """The first JSON array in the model's reply. Anything else is an empty result."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


async def extract_from_conversation(services: Services, conversation_id: str) -> Extraction:
    store = services.memory
    local = services.router.local_adapter
    if store is None or not store.enabled:
        raise InvalidMemory("Memory is switched off.")
    if local is None:
        raise ModelsUnavailable(
            "No local model is configured, and extraction never uses the cloud."
        )

    async with services.sessionmaker() as session:
        conv = await repo.get_conversation(session, conversation_id)  # 404 if missing
        messages = await repo.all_messages(session, conversation_id)
    privacy = "local_only" if conv.local_only else "normal"
    out = Extraction()
    if not any(m.role == "user" for m in messages):
        return out

    try:
        response = await local.complete(
            ModelRequest(
                messages=[
                    ChatMessage("system", INSTRUCTION),
                    ChatMessage("user", _transcript(messages)),
                ],
                max_output_tokens=600,
                temperature=0.0,
            )
        )
    except ModelError as exc:
        raise ModelsUnavailable(
            "The local model could not run the extraction.", extra={"reason": type(exc).__name__}
        ) from exc

    seen: set[str] = set()
    for item in parse_items(response.content)[: services.settings.memory_extract_max * 2]:
        if len(out.proposals) >= services.settings.memory_extract_max:
            break
        kind, text = item.get("kind"), item.get("text")
        if kind not in MEMORY_KINDS or not isinstance(text, str):
            out.dropped_invalid += 1
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            out.duplicates += 1
            continue
        seen.add(key)
        try:
            created = await store.create(
                text,
                kind=kind,
                privacy=privacy,
                source="extracted",
                status="proposed",
                conversation_id=conversation_id,
                actor="model_extraction",
            )
        except SecretRejected:
            out.dropped_secret += 1
            continue
        except InvalidMemory:
            out.dropped_invalid += 1
            continue
        if created.duplicate_of:
            out.duplicates += 1
        else:
            out.proposals.append(created.memory)
    return out
