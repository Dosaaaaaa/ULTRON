"""ULTRON backend application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request

from app.api.v1 import chat, devices, health, memory, ws
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import register_secrets, setup_logging
from app.core.personality import PersonalityEngine, load_personality
from app.core.services import Services, SlidingWindowLimiter
from app.db.session import create_engine, create_sessionmaker, migrate_to_head
from app.devices import reminders
from app.devices.hub import PushHub
from app.devices.registry import DeviceRegistry
from app.memory.embedder import Embedder, OllamaEmbedder
from app.memory.store import MemoryStore
from app.models.base import ModelAdapter
from app.models.budget import BudgetedAdapter, CloudBudget, Limits
from app.models.ollama import OllamaAdapter
from app.models.openai_compat import OpenAICompatAdapter
from app.models.router import ModelRouter
from app.tools.web import BraveSearch, DailyCap, SearchProvider, SearxngSearch

log = logging.getLogger("ultron")


def create_app(
    settings: Settings | None = None,
    *,
    cloud: ModelAdapter | None = None,
    local: ModelAdapter | None = None,
    embedder: Embedder | None = None,
    web_search: SearchProvider | None = None,
) -> FastAPI:
    """`cloud`/`local`/`embedder` let tests inject fakes; production builds them from settings."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    register_secrets(settings.secret_values())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)  # also creates a missing sqlite directory
        if settings.auto_migrate:
            await migrate_to_head(settings.database_url)
        http = httpx.AsyncClient()

        cloud_adapter = cloud
        if cloud_adapter is None and settings.allow_cloud and settings.cloud_configured:
            cloud_adapter = OpenAICompatAdapter(
                client=http,
                base_url=settings.cloud_base_url,
                api_key=settings.cloud_api_key.get_secret_value(),  # type: ignore[union-attr]
                model=settings.cloud_model,
                provider=settings.cloud_provider_name,
                timeout_s=settings.cloud_timeout_s,
            )
            limits = Limits(
                tokens_per_day=settings.cloud_limit_tokens_per_day,
                tokens_per_minute=settings.cloud_limit_tokens_per_minute,
                requests_per_day=settings.cloud_limit_requests_per_day,
                requests_per_minute=settings.cloud_limit_requests_per_minute,
                safety=settings.cloud_budget_safety,
            )
            # Wrapped even with no limits configured: a 429 then still sets a cooldown, so the
            # next requests go straight to the local model instead of each paying for a failure.
            cloud_adapter = BudgetedAdapter(
                cloud_adapter,
                CloudBudget(limits, path=settings.cloud_budget_file if limits.enabled else None),
            )
        local_adapter = local or OllamaAdapter(
            client=http,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_s=settings.ollama_timeout_s,
            think=settings.ollama_think,
        )
        personality = PersonalityEngine(
            load_personality(settings.prompts_dir, settings.personality_version)
        )
        sessionmaker = create_sessionmaker(engine)
        embedder_ = embedder
        if embedder_ is None and settings.memory_enabled:
            embedder_ = OllamaEmbedder(
                http,
                settings.ollama_base_url,
                settings.embedding_model,
                settings.embedding_timeout_s,
            )
        memory = MemoryStore(sessionmaker, embedder_, settings) if settings.memory_enabled else None
        if memory is not None:
            await memory.purge_expired()  # retention: expired memories are removed at start-up
        web_provider = web_search
        if web_provider is None and settings.web_search_provider != "none":
            if not settings.allow_cloud or settings.routing_policy == "local_only":
                # "nothing leaves the device" covers search too, however it was configured
                log.warning(
                    "web_search_disabled",
                    extra={"reason": "cloud disabled or routing policy local_only"},
                )
            elif settings.web_search_provider == "brave":
                web_provider = BraveSearch(
                    http,
                    settings.web_search_api_key.get_secret_value(),  # type: ignore[union-attr]
                    settings.web_search_timeout_s,
                )
            else:
                web_provider = SearxngSearch(
                    http, settings.web_search_url, settings.web_search_timeout_s
                )
        registry = DeviceRegistry(sessionmaker)
        hub = PushHub(sessionmaker, registry)
        app.state.services = Services(
            settings=settings,
            engine=engine,
            sessionmaker=sessionmaker,
            http=http,
            router=ModelRouter(
                cloud=cloud_adapter,
                local=local_adapter,
                policy=settings.routing_policy,
                allow_cloud=settings.allow_cloud,
                enforce_style=settings.enforce_style,
            ),
            personality=personality,
            limiter=SlidingWindowLimiter(settings.rate_limit_per_minute),
            memory=memory,
            web=web_provider,
            web_cap=DailyCap(settings.web_search_max_per_day) if web_provider else None,
            devices=registry,
            hub=hub,
            pair_limiter=SlidingWindowLimiter(5),
        )
        log.info(
            "ultron_started",
            extra={
                "personality_version": personality.personality.version,
                "routing_policy": settings.routing_policy,
                "cloud_configured": cloud_adapter is not None,
                "database": engine.url.render_as_string(hide_password=True),
            },
        )
        try:
            if settings.scheduler_enabled:
                async with reminders.running(app.state.services):
                    yield
            else:
                yield
        finally:
            await http.aclose()
            await engine.dispose()

    app = FastAPI(title="ULTRON", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        log.info(
            "request",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return response

    app.include_router(health.live_router)
    app.include_router(health.router, prefix="/v1")
    app.include_router(chat.router, prefix="/v1")
    app.include_router(memory.router, prefix="/v1")
    app.include_router(ws.router, prefix="/v1")
    app.include_router(devices.router, prefix="/v1")
    return app


def app_factory() -> FastAPI:
    """uvicorn --factory entry point: `uvicorn app.main:app_factory --factory`."""
    return create_app()
