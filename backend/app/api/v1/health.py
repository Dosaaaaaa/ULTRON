from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import Principal, ServicesDep

# Unauthenticated liveness only; nothing here reveals configuration.
live_router = APIRouter(tags=["health"])
# Authenticated detail.
router = APIRouter(tags=["health"])


@live_router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(services: ServicesDep, _: Principal) -> dict:
    try:
        async with services.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        db = {"ok": True}
    except Exception:  # noqa: BLE001 - report, never raise, from a health probe
        db = {"ok": False}

    engines = [asdict(await a.health()) for a in services.router.adapters]
    usable = any(e["reachable"] is not False for e in engines)
    budgets = [a.budget.snapshot() for a in services.router.adapters if hasattr(a, "budget")]
    return {
        "status": "ok" if db["ok"] and usable else "degraded",
        "database": db,
        "engines": engines,
        "cloud_configured": services.settings.cloud_configured,
        "cloud_allowed": services.settings.allow_cloud,
        "cloud_budget": budgets[0] if budgets else None,
        "routing_policy": services.settings.routing_policy,
        "memory": {"enabled": services.memory is not None and services.memory.enabled},
        "personality": {
            "version": services.personality.personality.version,
            "sha256": services.personality.personality.sha256[:12],
            "local_sha256": (services.personality.personality.local_sha256 or "")[:12] or None,
        },
    }


@router.get("/usage")
async def usage(services: ServicesDep, _: Principal) -> dict:
    """Per-model counters since process start (in-memory; per-message data is persisted)."""
    return services.router.usage()
