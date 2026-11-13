"""Long-term memory: storage, search, and the rules that govern what may be remembered.

Categories (spec section 5):
- working memory   = the conversation window (not stored here)
- episodic         = kind "episodic": what happened in earlier conversations
- semantic         = kind "semantic": retained facts about the operator or the world
- project memory   = kind "project": ongoing work, objectives, progress (linked to a project)

Provenance (spec: distinguish user-confirmed / retrieved / assumed / temporary):
- status "confirmed": the operator said it or approved it. Only these are ever used as facts.
- status "proposed":  a model suggested it. Kept for review, NEVER retrieved into a prompt.
- "retrieved" is what search returns at answer time; "temporary" context is never stored.

Nothing is saved automatically from a normal turn. Memories are created by an explicit "remember
that ..." command, by the operator through the API/UI, or as proposals the operator must confirm.
Secrets are refused. Every change writes a content-free audit event.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, cast, delete, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError, NotFound
from app.db.models import (
    MEMORY_KINDS,
    MEMORY_PRIVACY,
    MEMORY_SOURCES,
    PROJECT_STATUSES,
    Memory,
    MemoryEvent,
    Project,
    utcnow,
)
from app.memory.embedder import Embedder, EmbeddingUnavailable
from app.memory.guard import contains_secret

log = logging.getLogger("ultron.memory")

DELETE_ALL_PHRASE = "delete-all-memories"


class InvalidMemory(AppError):
    status_code = 422
    code = "invalid_memory"


class SecretRejected(InvalidMemory):
    code = "memory_contains_secret"


class MemoryDisabled(AppError):
    status_code = 503
    code = "memory_disabled"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


@dataclass
class Hit:
    memory: Memory
    similarity: float


@dataclass
class Created:
    memory: Memory
    embedded: bool  # False when the embedder was unavailable: stored, but not yet searchable
    duplicate_of: str | None = None  # an existing memory this one repeats (nothing new was saved)


def clean(content: str, max_chars: int) -> str:
    text = " ".join(content.split())  # one line, single spaces: it is later placed in a prompt
    if not text:
        raise InvalidMemory("Memory text must not be blank.")
    if len(text) > max_chars:
        raise InvalidMemory(f"Memory text is longer than {max_chars} characters.")
    if "\x00" in content:
        raise InvalidMemory("Memory text contains an invalid character.")
    if contains_secret(text):
        raise SecretRejected(
            "That looks like a credential or other secret, so it was not saved. "
            "Memory is not a place for passwords, keys or card numbers."
        )
    return text


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


class MemoryStore:
    def __init__(
        self, sessionmaker: async_sessionmaker, embedder: Embedder | None, settings: Settings
    ) -> None:
        self._sm = sessionmaker
        self.embedder = embedder
        self.s = settings

    @property
    def enabled(self) -> bool:
        return self.s.memory_enabled and self.embedder is not None

    # ------------------------------------------------------------------ helpers
    async def _embed(self, text: str, *, query: bool = False) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            return (await self.embedder.embed([text], query=query))[0]
        except EmbeddingUnavailable as exc:
            log.warning("embedder_unavailable", extra={"kind": exc.kind})
            return None

    @staticmethod
    def _event(
        session: AsyncSession, action: str, memory_id: str | None, actor: str, **detail
    ) -> None:
        # ids and counts only; memory text must never be written to the audit trail
        session.add(
            MemoryEvent(action=action, memory_id=memory_id, actor=actor, detail=detail or None)
        )
        log.info("memory_event", extra={"action": action, "memory_id": memory_id, "actor": actor})

    async def _require_project(self, session: AsyncSession, project_id: str | None) -> None:
        if project_id is not None and await session.get(Project, project_id) is None:
            raise NotFound("No such project.")

    # ------------------------------------------------------------------ create
    async def create(
        self,
        content: str,
        *,
        kind: str = "semantic",
        project_id: str | None = None,
        privacy: str = "normal",
        source: str = "user",
        status: str = "confirmed",
        conversation_id: str | None = None,
        retention_days: int | None = None,
        actor: str = "user",
    ) -> Created:
        if not self.s.memory_enabled:
            raise MemoryDisabled("Memory is switched off (ULTRON_MEMORY_ENABLED=false).")
        if kind not in MEMORY_KINDS:
            raise InvalidMemory(f"kind must be one of {MEMORY_KINDS}.")
        if privacy not in MEMORY_PRIVACY or source not in MEMORY_SOURCES:
            raise InvalidMemory("Unknown privacy or source value.")
        if status not in ("confirmed", "proposed"):
            raise InvalidMemory("Unknown status.")
        if retention_days is not None and retention_days < 1:
            raise InvalidMemory("retention_days must be at least 1.")
        text = clean(content, self.s.memory_max_chars)
        if retention_days is None and kind == "episodic" and self.s.memory_episodic_retention_days:
            retention_days = self.s.memory_episodic_retention_days
        vec = await self._embed(text)

        async with self._sm() as session:
            await self._require_project(session, project_id)
            if vec is not None:
                # A suggestion is also compared with earlier suggestions, so asking for suggestions
                # twice does not fill the review queue with copies. A note the operator states
                # themselves is compared with confirmed notes only: it must not be swallowed by a
                # suggestion that is not in use.
                seen = ("confirmed", "proposed") if status == "proposed" else ("confirmed",)
                dup = await self._nearest(session, vec, project_id, seen)
                if dup and dup.similarity >= self.s.memory_dedupe_similarity:
                    return Created(dup.memory, embedded=True, duplicate_of=dup.memory.id)
            now = utcnow()
            mem = Memory(
                kind=kind,
                content=text,
                status=status,
                source=source,
                privacy=privacy,
                project_id=project_id,
                source_conversation_id=conversation_id,
                embedding=vec,
                embedding_model=self.embedder.model if vec is not None and self.embedder else None,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(days=retention_days) if retention_days else None,
            )
            session.add(mem)
            await session.flush()
            self._event(session, "create", mem.id, actor, kind=kind, status=status, source=source)
            await session.commit()
        return Created(mem, embedded=vec is not None)

    # ------------------------------------------------------------------ read
    async def get(self, memory_id: str) -> Memory:
        async with self._sm() as session:
            mem = await session.get(Memory, memory_id)
        if mem is None:
            raise NotFound("No such memory.")
        return mem

    async def list_memories(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Memory], int]:
        """Newest first. `q` is a plain text filter (not semantic; use search for that)."""
        conds = []
        if kind:
            conds.append(Memory.kind == kind)
        if status:
            conds.append(Memory.status == status)
        if project_id:
            conds.append(Memory.project_id == project_id)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append(Memory.content.ilike(f"%{escaped}%", escape="\\"))
        async with self._sm() as session:
            total = await session.scalar(select(func.count()).select_from(Memory).where(*conds))
            rows = (
                await session.scalars(
                    select(Memory)
                    .where(*conds)
                    .order_by(Memory.created_at.desc(), Memory.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        return list(rows), total or 0

    # ------------------------------------------------------------------ search
    def _scope(
        self,
        project_id: str | None,
        include_private: bool,
        now: datetime,
        statuses: tuple[str, ...] = ("confirmed",),
    ):
        conds = [
            Memory.status.in_(statuses),
            Memory.embedding.is_not(None),
            Memory.embedding_model == (self.embedder.model if self.embedder else None),
            or_(Memory.expires_at.is_(None), Memory.expires_at > now),
        ]
        if project_id is not None:  # that project plus everything not tied to a project
            conds.append(or_(Memory.project_id.is_(None), Memory.project_id == project_id))
        if not include_private:
            conds.append(Memory.privacy == "normal")
        return conds

    async def _nearest(
        self,
        session: AsyncSession,
        vec: list[float],
        project_id: str | None,
        statuses: tuple[str, ...] = ("confirmed",),
    ) -> Hit | None:
        hits = await self._rank(
            session, vec, 1, project_id=project_id, include_private=True, statuses=statuses
        )
        return hits[0] if hits else None

    async def _rank(
        self,
        session: AsyncSession,
        vec: list[float],
        k: int,
        *,
        project_id: str | None,
        include_private: bool,
        statuses: tuple[str, ...] = ("confirmed",),
    ) -> list[Hit]:
        conds = self._scope(project_id, include_private, utcnow(), statuses)
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            # return_type: the operator yields a float, not another vector
            distance = Memory.embedding.op("<=>", return_type=Float)(
                cast(literal(_vec_literal(vec)), Vector())
            )
            stmt = (
                select(Memory, distance.label("distance"))
                .where(*conds, func.vector_dims(Memory.embedding) == len(vec))
                .order_by(distance)
                .limit(k)
            )
            return [Hit(m, 1.0 - float(d)) for m, d in (await session.execute(stmt)).all()]
        rows = (await session.scalars(select(Memory).where(*conds))).all()
        rows = [m for m in rows if m.embedding and len(m.embedding) == len(vec)]
        if not rows:
            return []
        sims = np.asarray([m.embedding for m in rows], dtype=np.float32) @ np.asarray(
            vec, dtype=np.float32
        )
        order = np.argsort(-sims)[:k]
        return [Hit(rows[i], float(sims[i])) for i in order]

    async def search(
        self,
        query: str,
        *,
        k: int | None = None,
        min_similarity: float | None = None,
        project_id: str | None = None,
        include_private: bool = True,
    ) -> list[Hit]:
        """Semantic search over CONFIRMED, unexpired memories. Raises EmbeddingUnavailable."""
        if not self.enabled:
            return []
        assert self.embedder is not None
        vec = (await self.embedder.embed([query], query=True))[0]
        k = k or self.s.memory_top_k
        floor = self.s.memory_min_similarity if min_similarity is None else min_similarity
        async with self._sm() as session:
            hits = await self._rank(
                session, vec, k, project_id=project_id, include_private=include_private
            )
        return [h for h in hits if h.similarity >= floor and not math.isnan(h.similarity)]

    async def touch(self, ids: list[str]) -> None:
        if not ids:
            return
        now = utcnow()
        async with self._sm() as session:
            for mem in (await session.scalars(select(Memory).where(Memory.id.in_(ids)))).all():
                mem.last_used_at = now
                mem.use_count = (mem.use_count or 0) + 1
            await session.commit()

    # ------------------------------------------------------------------ change
    async def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        kind: str | None = None,
        privacy: str | None = None,
        project_id: str | None = None,
        clear_project: bool = False,
        retention_days: int | None = None,
        clear_retention: bool = False,
        actor: str = "user",
    ) -> Created:
        """Correct a memory. Changed text is re-checked for secrets and re-embedded."""
        if kind is not None and kind not in MEMORY_KINDS:
            raise InvalidMemory(f"kind must be one of {MEMORY_KINDS}.")
        if privacy is not None and privacy not in MEMORY_PRIVACY:
            raise InvalidMemory("Unknown privacy value.")
        if retention_days is not None and retention_days < 1:
            raise InvalidMemory("retention_days must be at least 1.")
        text = clean(content, self.s.memory_max_chars) if content is not None else None
        vec = await self._embed(text) if text is not None else None
        async with self._sm() as session:
            mem = await session.get(Memory, memory_id)
            if mem is None:
                raise NotFound("No such memory.")
            changed: list[str] = []
            if text is not None and text != mem.content:
                mem.content = text
                mem.embedding = vec
                mem.embedding_model = (
                    self.embedder.model if vec is not None and self.embedder else None
                )
                changed.append("content")
            if kind is not None and kind != mem.kind:
                mem.kind = kind
                changed.append("kind")
            if privacy is not None and privacy != mem.privacy:
                mem.privacy = privacy
                changed.append("privacy")
            if clear_project:
                mem.project_id = None
                changed.append("project")
            elif project_id is not None and project_id != mem.project_id:
                await self._require_project(session, project_id)
                mem.project_id = project_id
                changed.append("project")
            if clear_retention:
                mem.expires_at = None
                changed.append("retention")
            elif retention_days is not None:
                mem.expires_at = utcnow() + timedelta(days=retention_days)
                changed.append("retention")
            if changed:
                mem.updated_at = utcnow()
                self._event(session, "update", mem.id, actor, fields=changed)
            await session.commit()
        return Created(mem, embedded=mem.embedding is not None)

    async def attach_to_conversation(self, memory_id: str, conversation_id: str) -> None:
        """Link a memory saved by a command to the conversation it was said in (best effort)."""
        async with self._sm() as session:
            mem = await session.get(Memory, memory_id)
            if mem is not None and mem.source_conversation_id is None:
                mem.source_conversation_id = conversation_id
                await session.commit()

    async def confirm(self, memory_id: str, actor: str = "user") -> Memory:
        async with self._sm() as session:
            mem = await session.get(Memory, memory_id)
            if mem is None:
                raise NotFound("No such memory.")
            if mem.status != "proposed":
                raise Conflict("That memory is already confirmed.")
            mem.status = "confirmed"
            mem.updated_at = utcnow()
            self._event(session, "confirm", mem.id, actor)
            await session.commit()
        return mem

    async def delete(self, memory_id: str, *, actor: str = "user", action: str = "delete") -> None:
        async with self._sm() as session:
            mem = await session.get(Memory, memory_id)
            if mem is None:
                raise NotFound("No such memory.")
            await session.delete(mem)
            self._event(session, action, memory_id, actor)
            await session.commit()

    async def delete_all(
        self, confirm: str, *, status: str | None = None, actor: str = "user"
    ) -> int:
        if confirm != DELETE_ALL_PHRASE:
            raise InvalidMemory(f"Pass confirm={DELETE_ALL_PHRASE} to delete every memory.")
        async with self._sm() as session:
            stmt = delete(Memory)
            if status:
                stmt = stmt.where(Memory.status == status)
            result = await session.execute(stmt)
            count = result.rowcount or 0
            self._event(session, "delete_all", None, actor, count=count, status=status)
            await session.commit()
        return count

    async def discard_proposals_from(self, conversation_id: str) -> int:
        """Remove unconfirmed suggestions that were derived from a conversation being erased.
        Confirmed notes are the operator's own and stay (their link to the chat is dropped)."""
        async with self._sm() as session:
            result = await session.execute(
                delete(Memory).where(
                    Memory.source_conversation_id == conversation_id, Memory.status == "proposed"
                )
            )
            count = result.rowcount or 0
            if count:
                self._event(session, "discard_proposals", None, "system", count=count)
            await session.commit()
        return count

    async def purge_expired(self) -> int:
        async with self._sm() as session:
            result = await session.execute(
                delete(Memory).where(Memory.expires_at.is_not(None), Memory.expires_at <= utcnow())
            )
            count = result.rowcount or 0
            if count:
                self._event(session, "purge", None, "system", count=count)
            await session.commit()
        return count

    async def reindex(self) -> dict[str, int]:
        """Embed memories that have no vector yet, or one from a different model."""
        if self.embedder is None:
            raise MemoryDisabled("No embedder is configured.")
        done = failed = 0
        async with self._sm() as session:
            rows = (
                await session.scalars(
                    select(Memory).where(
                        or_(
                            Memory.embedding.is_(None),
                            Memory.embedding_model.is_distinct_from(self.embedder.model),
                        )
                    )
                )
            ).all()
            for mem in rows:
                vec = await self._embed(mem.content)
                if vec is None:
                    failed += 1
                    continue
                mem.embedding, mem.embedding_model = vec, self.embedder.model
                done += 1
            if done:
                self._event(session, "reindex", None, "user", count=done)
            await session.commit()
        return {"embedded": done, "failed": failed}

    async def counts(self) -> dict[str, int]:
        async with self._sm() as session:
            rows = (
                await session.execute(select(Memory.status, func.count()).group_by(Memory.status))
            ).all()
            unembedded = await session.scalar(
                select(func.count()).select_from(Memory).where(Memory.embedding.is_(None))
            )
        out = {"confirmed": 0, "proposed": 0, **{s: n for s, n in rows}}
        out["not_embedded"] = unembedded or 0
        return out

    # ------------------------------------------------------------------ projects
    async def create_project(self, name: str, description: str = "") -> Project:
        name = " ".join(name.split())
        if not name or len(name) > 120:
            raise InvalidMemory("Project name must be 1-120 characters.")
        if contains_secret(name) or contains_secret(description):
            raise SecretRejected("A project name or description looks like it holds a secret.")
        async with self._sm() as session:
            if await session.scalar(select(Project).where(Project.name == name)):
                raise Conflict("A project with that name already exists.")
            proj = Project(name=name, description=description.strip())
            session.add(proj)
            await session.flush()
            self._event(session, "project_create", None, "user", project_id=proj.id)
            await session.commit()
        return proj

    async def list_projects(self) -> list[tuple[Project, int]]:
        async with self._sm() as session:
            rows = (
                await session.execute(
                    select(Project, func.count(Memory.id))
                    .outerjoin(Memory, Memory.project_id == Project.id)
                    .group_by(Project.id)
                    .order_by(Project.name)
                )
            ).all()
        return [(p, n) for p, n in rows]

    async def get_project(self, project_id: str) -> Project:
        async with self._sm() as session:
            proj = await session.get(Project, project_id)
        if proj is None:
            raise NotFound("No such project.")
        return proj

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> Project:
        if status is not None and status not in PROJECT_STATUSES:
            raise InvalidMemory(f"status must be one of {PROJECT_STATUSES}.")
        async with self._sm() as session:
            proj = await session.get(Project, project_id)
            if proj is None:
                raise NotFound("No such project.")
            if name is not None:
                name = " ".join(name.split())
                if not name or len(name) > 120:
                    raise InvalidMemory("Project name must be 1-120 characters.")
                clash = await session.scalar(
                    select(Project).where(Project.name == name, Project.id != project_id)
                )
                if clash:
                    raise Conflict("A project with that name already exists.")
                proj.name = name
            if description is not None:
                if contains_secret(description):
                    raise SecretRejected("The description looks like it holds a secret.")
                proj.description = description.strip()
            if status is not None:
                proj.status = status
            proj.updated_at = utcnow()
            self._event(session, "project_update", None, "user", project_id=project_id)
            await session.commit()
        return proj

    async def delete_project(self, project_id: str) -> int:
        """Deletes the project AND its memories (the memories are about that project)."""
        async with self._sm() as session:
            proj = await session.get(Project, project_id)
            if proj is None:
                raise NotFound("No such project.")
            count = await session.scalar(
                select(func.count()).select_from(Memory).where(Memory.project_id == project_id)
            )
            await session.delete(proj)
            self._event(
                session, "project_delete", None, "user", project_id=project_id, memories=count
            )
            await session.commit()
        return count or 0

    # ------------------------------------------------------------------ export
    async def export(self) -> list[dict]:
        """Everything stored, as plain data, without the vectors."""
        async with self._sm() as session:
            rows = (await session.scalars(select(Memory).order_by(Memory.created_at))).all()
            self._event(session, "export", None, "user", count=len(rows))
            await session.commit()
        return [memory_dict(m) for m in rows]


def memory_dict(m: Memory) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "content": m.content,
        "status": m.status,
        "source": m.source,
        "privacy": m.privacy,
        "project_id": m.project_id,
        "source_conversation_id": m.source_conversation_id,
        "embedded": m.embedding is not None,
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
        "last_used_at": m.last_used_at.isoformat() if m.last_used_at else None,
        "use_count": m.use_count or 0,
        "expires_at": m.expires_at.isoformat() if m.expires_at else None,
    }


__all__ = [
    "DELETE_ALL_PHRASE",
    "Conflict",
    "Created",
    "Hit",
    "InvalidMemory",
    "MemoryDisabled",
    "MemoryStore",
    "SecretRejected",
    "clean",
    "memory_dict",
]
