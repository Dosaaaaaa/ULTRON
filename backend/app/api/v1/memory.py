"""Memory endpoints: inspect, correct, delete, confirm proposals, search, projects, extract."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import Limited, Principal, ServicesDep
from app.core.errors import AppError
from app.memory.embedder import EmbeddingUnavailable
from app.memory.extract import extract_from_conversation
from app.memory.store import MemoryDisabled, MemoryStore, memory_dict

router = APIRouter(tags=["memory"])

UUID = Path(pattern=r"^[0-9a-fA-F-]{36}$")
IdPath = Annotated[str, UUID]
MAX_TEXT = 4000  # the store applies the (smaller) configured per-memory limit


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_TEXT)
    kind: str = "semantic"
    project_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    privacy: str = "normal"
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    kind: str | None = None
    privacy: str | None = None
    project_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    clear_project: bool = False
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    clear_retention: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=20)
    project_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = None


def store_of(services) -> MemoryStore:
    if services.memory is None:
        raise MemoryDisabled("Memory is switched off (ULTRON_MEMORY_ENABLED=false).")
    return services.memory


def _project(p, memories: int | None = None) -> dict:
    out = {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "status": p.status,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }
    if memories is not None:
        out["memory_count"] = memories
    return out


# ---------------------------------------------------------------- status
@router.get("/memory/status")
async def memory_status(services: ServicesDep, _: Principal) -> dict:
    s = services.settings
    if services.memory is None:
        return {"enabled": False}
    store = services.memory
    return {
        "enabled": True,
        "embedder": {
            "model": store.embedder.model if store.embedder else None,
            "available": await store.embedder.available() if store.embedder else False,
            "location": "local (Ollama)",  # memory text is never embedded by a cloud service
        },
        "counts": await store.counts(),
        "allow_cloud": s.memory_allow_cloud,
        "episodic_retention_days": s.memory_episodic_retention_days or None,
        "min_similarity": s.memory_min_similarity,
        "top_k": s.memory_top_k,
    }


# ---------------------------------------------------------------- memories
@router.get("/memories")
async def list_memories(
    services: ServicesDep,
    _: Principal,
    kind: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    project_id: Annotated[str | None, Query(pattern=r"^[0-9a-fA-F-]{36}$")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    rows, total = await store_of(services).list_memories(
        kind=kind, status=status_, project_id=project_id, q=q, limit=limit, offset=offset
    )
    return {"total": total, "items": [memory_dict(m) for m in rows]}


@router.post("/memories", status_code=status.HTTP_201_CREATED)
async def create_memory(body: MemoryCreate, services: ServicesDep, _: Principal) -> dict:
    """The operator saves a memory directly (confirmed). A repeat of an existing one is skipped."""
    created = await store_of(services).create(
        body.content,
        kind=body.kind,
        project_id=body.project_id,
        privacy=body.privacy,
        source="api",
        retention_days=body.retention_days,
    )
    return {
        "memory": memory_dict(created.memory),
        "embedded": created.embedded,
        "duplicate_of": created.duplicate_of,
    }


@router.post("/memories/search")
async def search_memories(body: SearchRequest, services: ServicesDep, _: Limited) -> dict:
    store = store_of(services)
    try:
        hits = await store.search(
            body.query,
            k=body.limit,
            min_similarity=body.min_similarity,
            project_id=body.project_id,
        )
    except EmbeddingUnavailable as exc:
        raise AppError(
            "Semantic search is unavailable: the local embedding model is not answering.",
            extra={"reason": exc.kind},
        ) from exc
    return {
        "results": [{**memory_dict(h.memory), "similarity": round(h.similarity, 4)} for h in hits]
    }


@router.post("/memories/reindex")
async def reindex(services: ServicesDep, _: Limited) -> dict:
    """Embed memories saved while the embedder was down (or by a different embedding model)."""
    return await store_of(services).reindex()


@router.get("/memories/export")
async def export_memories(services: ServicesDep, _: Principal) -> dict:
    items = await store_of(services).export()
    return {"count": len(items), "items": items}


@router.delete("/memories")
async def delete_all_memories(
    services: ServicesDep,
    _: Principal,
    confirm: str = "",
    status_: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    """Deletes every memory (or all with one status). Needs confirm=delete-all-memories."""
    return {"deleted": await store_of(services).delete_all(confirm, status=status_)}


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: IdPath, services: ServicesDep, _: Principal) -> dict:
    return memory_dict(await store_of(services).get(memory_id))


@router.patch("/memories/{memory_id}")
async def patch_memory(
    memory_id: IdPath, body: MemoryPatch, services: ServicesDep, _: Principal
) -> dict:
    """Correct a memory. Changed text is re-checked for secrets and re-embedded."""
    created = await store_of(services).update(
        memory_id,
        content=body.content,
        kind=body.kind,
        privacy=body.privacy,
        project_id=body.project_id,
        clear_project=body.clear_project,
        retention_days=body.retention_days,
        clear_retention=body.clear_retention,
    )
    return {"memory": memory_dict(created.memory), "embedded": created.embedded}


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: IdPath, services: ServicesDep, _: Principal) -> Response:
    await store_of(services).delete(memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/memories/{memory_id}/confirm")
async def confirm_memory(memory_id: IdPath, services: ServicesDep, _: Principal) -> dict:
    """Accept a model-proposed memory. Only confirmed memories are ever used in answers."""
    return memory_dict(await store_of(services).confirm(memory_id))


@router.post("/memories/{memory_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_memory(memory_id: IdPath, services: ServicesDep, _: Principal) -> Response:
    await store_of(services).delete(memory_id, action="reject")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/extract-memories")
async def extract_memories(conversation_id: IdPath, services: ServicesDep, _: Limited) -> dict:
    """Ask the LOCAL model to suggest memories from a conversation. They arrive as proposals."""
    result = await extract_from_conversation(services, conversation_id)
    return {
        "proposals": [memory_dict(m) for m in result.proposals],
        "duplicates": result.duplicates,
        "dropped_secret": result.dropped_secret,
        "dropped_invalid": result.dropped_invalid,
        "engine": "local",
    }


# ---------------------------------------------------------------- projects
@router.get("/projects")
async def list_projects(services: ServicesDep, _: Principal) -> list[dict]:
    return [_project(p, n) for p, n in await store_of(services).list_projects()]


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, services: ServicesDep, _: Principal) -> dict:
    return _project(await store_of(services).create_project(body.name, body.description))


@router.get("/projects/{project_id}")
async def get_project(project_id: IdPath, services: ServicesDep, _: Principal) -> dict:
    return _project(await store_of(services).get_project(project_id))


@router.patch("/projects/{project_id}")
async def patch_project(
    project_id: IdPath, body: ProjectPatch, services: ServicesDep, _: Principal
) -> dict:
    return _project(
        await store_of(services).update_project(
            project_id, name=body.name, description=body.description, status=body.status
        )
    )


@router.delete("/projects/{project_id}")
async def delete_project(project_id: IdPath, services: ServicesDep, _: Principal) -> dict:
    """Deletes the project and the memories that belong to it."""
    return {"deleted_memories": await store_of(services).delete_project(project_id)}
