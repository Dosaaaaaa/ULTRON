from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.main import create_app
from app.models.base import ModelAdapter

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PROMPTS = Path(__file__).resolve().parents[2] / "shared" / "prompts"

# Opt-in: run the whole suite against a real PostgreSQL instead of per-test SQLite files.
# Never point this at a database you care about: every test drops the `public` schema.
PG_URL = os.environ.get("ULTRON_TEST_DATABASE_URL")


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        api_token=TOKEN,
        database_url=PG_URL or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        prompts_dir=PROMPTS,
        personality_version=1,  # existing assertions are written against v1; v2 tested separately
        scheduler_enabled=False,  # reminder tests call fire_due() themselves
        memory_enabled=False,  # memory tests opt in (with a fake embedder); never call Ollama
        _env_file=None,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
async def _clean_postgres():
    if not PG_URL:
        yield
        return
    engine = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    yield


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def run_app(tmp_path: Path) -> Callable:
    """Returns an async context manager yielding (client, app) with injected adapters."""

    @asynccontextmanager
    async def _run(
        cloud: ModelAdapter | None = None,
        local: ModelAdapter | None = None,
        embedder: object | None = None,
        web_search: object | None = None,
        **setting_overrides,
    ) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
        app = create_app(
            make_settings(tmp_path, **setting_overrides),
            cloud=cloud,
            local=local,
            embedder=embedder,
            web_search=web_search,
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, app

    return _run
