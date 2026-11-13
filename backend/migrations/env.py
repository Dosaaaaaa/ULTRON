"""Alembic environment (async). URL comes from Config.attributes or ULTRON_DATABASE_URL."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return (
        config.attributes.get("database_url")
        or os.environ.get("ULTRON_DATABASE_URL")
        or "sqlite+aiosqlite:///./data/ultron.db"
    )


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported")
run_migrations_online()
