"""Async engine/session factory and programmatic Alembic migration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]


def create_engine(database_url: str) -> AsyncEngine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    # hide_parameters: SQL errors must not embed bound values (user message text) in messages.
    engine = create_async_engine(database_url, hide_parameters=True)
    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_pragma(dbapi_conn, _record) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")  # enforce ON DELETE CASCADE
            # Deleted rows are overwritten with zeros instead of lingering in free pages, so an
            # erased conversation cannot be read back out of the database file.
            cur.execute("PRAGMA secure_delete=ON")
            cur.close()

    return engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["database_url"] = database_url
    return cfg


async def migrate_to_head(database_url: str) -> None:
    # env.py runs its own event loop, so execute in a worker thread.
    await asyncio.to_thread(command.upgrade, alembic_config(database_url), "head")
