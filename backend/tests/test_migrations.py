from __future__ import annotations

import sqlite3

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base
from app.db.session import migrate_to_head


async def test_migration_creates_schema_matching_orm_models(tmp_path):
    db = tmp_path / "m.db"
    url = f"sqlite+aiosqlite:///{db}"
    await migrate_to_head(url)

    tables = {r[0] for r in sqlite3.connect(db).execute("select name from sqlite_master")}
    assert {"conversations", "messages", "alembic_version"} <= tables

    engine = create_async_engine(url)
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
        )
    await engine.dispose()
    assert diff == [], f"ORM models drifted from migrations: {diff}"


async def test_migration_is_idempotent(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'i.db'}"
    await migrate_to_head(url)
    await migrate_to_head(url)  # second run must be a no-op, not an error
