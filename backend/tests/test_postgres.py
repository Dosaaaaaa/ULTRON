"""PostgreSQL-only checks. Skipped unless ULTRON_TEST_DATABASE_URL points at a postgres DB.

These hit a real PostgreSQL server (the only thing that is real here; model adapters are fakes).
"""

from __future__ import annotations

import asyncio

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base, Message
from app.db.session import migrate_to_head
from tests.conftest import AUTH, PG_URL
from tests.fakes import FakeAdapter

pytestmark = pytest.mark.skipif(
    not (PG_URL and PG_URL.startswith("postgresql")), reason="ULTRON_TEST_DATABASE_URL not set"
)


async def chat(client, message, **extra):
    return await client.post("/v1/chat", json={"message": message, **extra}, headers=AUTH)


async def test_migration_runs_on_postgres_and_matches_models():
    await migrate_to_head(PG_URL)
    await migrate_to_head(PG_URL)  # idempotent
    engine = create_async_engine(PG_URL)
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda s: compare_metadata(MigrationContext.configure(s), Base.metadata)
        )
        version = await conn.scalar(text("select version_num from alembic_version"))
    await engine.dispose()
    assert diff == [] and version == "0003"


async def test_timestamps_are_utc_aware_and_ordered(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        cid = (await chat(c, "a")).json()["conversation_id"]
        msgs = (await c.get(f"/v1/conversations/{cid}", headers=AUTH)).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert all(m["created_at"].endswith("Z") or "+00:00" in m["created_at"] for m in msgs)
    assert msgs[0]["created_at"] <= msgs[1]["created_at"]


async def test_unicode_and_max_length_roundtrip(run_app):
    text_in = "ΩΣ 日本語 🙂 " + "x" * 7900
    async with run_app(None, FakeAdapter("local", reply="réponse ünï 日本語")) as (c, _):
        cid = (await chat(c, text_in)).json()["conversation_id"]
        msgs = (await c.get(f"/v1/conversations/{cid}", headers=AUTH)).json()["messages"]
    assert msgs[0]["content"] == text_in and msgs[1]["content"] == "réponse ünï 日本語"


async def test_concurrent_conversations_and_same_conversation_ordering(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        results = await asyncio.gather(*(chat(c, f"m{i}") for i in range(12)))
        assert all(r.status_code == 200 for r in results)
        cid = results[0].json()["conversation_id"]
        await asyncio.gather(*(chat(c, f"s{i}", conversation_id=cid) for i in range(6)))
        async with app.state.services.sessionmaker() as s:
            total = await s.scalar(select(func.count(Message.id)))
            ids = list(await s.scalars(select(Message.id).where(Message.conversation_id == cid)))
            roles = list(
                await s.scalars(
                    select(Message.role).where(Message.conversation_id == cid).order_by(Message.id)
                )
            )
    assert total == 12 * 2 + 6 * 2
    assert len(ids) == 14
    assert roles == ["user", "assistant"] * 7  # exchanges never interleave


async def test_delete_cascades_in_postgres(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, app):
        cid = (await chat(c, "x")).json()["conversation_id"]
        assert (await c.delete(f"/v1/conversations/{cid}", headers=AUTH)).status_code == 204
        async with app.state.services.sessionmaker() as s:
            assert await s.scalar(select(func.count(Message.id))) == 0


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN GAP: PostgreSQL text cannot hold NUL (\\x00); input is not sanitised -> 500",
)
async def test_nul_character_in_message(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        c._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        r = await chat(c, "before\x00after")
    assert r.status_code == 200


async def test_failed_insert_does_not_log_message_body(run_app, capsys):
    """F3 on the real driver: the NUL failure used to dump SQL parameters (the user's text)."""
    async with run_app(None, FakeAdapter("local")) as (c, _):
        c._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        r = await chat(c, "SECRET-PG-BODY\x00tail")
    assert r.status_code == 500
    err = capsys.readouterr().err
    assert "unhandled_error" in err and "SECRET-PG-BODY" not in err


# ---------------------------------------------------------------- memory on pgvector
async def test_memory_column_is_a_real_pgvector_and_search_runs_in_sql(run_app):
    from tests.fakes import FakeAdapter, FakeEmbedder

    async with run_app(None, FakeAdapter("local"), embedder=FakeEmbedder(), memory_enabled=True,
                       memory_min_similarity=0.3) as (c, app):  # fmt: skip
        await c.post(
            "/v1/memories", json={"content": "The operator rows on the river Cam"}, headers=AUTH
        )
        async with app.state.services.sessionmaker() as s:
            kind = await s.scalar(text("select pg_typeof(embedding)::text from memories limit 1"))
            dims = await s.scalar(text("select vector_dims(embedding) from memories limit 1"))
            ext = await s.scalar(text("select extname from pg_extension where extname = 'vector'"))
        assert (kind, dims, ext) == ("vector", FakeEmbedder.DIM, "vector")
        hits = (
            await c.post("/v1/memories/search", json={"query": "rows river Cam"}, headers=AUTH)
        ).json()
        assert hits["results"][0]["similarity"] > 0.5  # real cosine, computed by pgvector


async def test_vectors_of_different_dimensions_coexist_without_breaking_search(run_app):
    from tests.fakes import FakeAdapter, FakeEmbedder

    class Small(FakeEmbedder):
        DIM = 8

    emb = FakeEmbedder("model-a")
    async with run_app(None, FakeAdapter("local"), embedder=emb, memory_enabled=True,
                       memory_min_similarity=0.3) as (c, app):  # fmt: skip
        await c.post(
            "/v1/memories", json={"content": "The operator rows on the river Cam"}, headers=AUTH
        )
        app.state.services.memory.embedder = small = Small("model-b")
        await c.post("/v1/memories", json={"content": "The operator climbs in Wales"}, headers=AUTH)
        async with app.state.services.sessionmaker() as s:
            assert sorted(
                (await s.scalars(text("select vector_dims(embedding) from memories"))).all()
            ) == [8, 1024]
        # each model only ever sees its own vectors; a dimension mismatch must not raise
        hits = (
            await c.post("/v1/memories/search", json={"query": "climbs Wales"}, headers=AUTH)
        ).json()
        assert [h["content"] for h in hits["results"]] == ["The operator climbs in Wales"]
        small.model = "model-a"  # same name, other dimension: the dimension guard must hold
        r = await c.post("/v1/memories/search", json={"query": "climbs Wales"}, headers=AUTH)
        assert r.status_code == 200
