"""Erasing a conversation: it is really gone, from the API and from the database file."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Memory
from tests.conftest import AUTH
from tests.fakes import FakeAdapter, FakeEmbedder

MEM = dict(memory_enabled=True, memory_min_similarity=0.3)


async def chat(c, message, **kw):
    r = await c.post("/v1/chat", json={"message": message, **kw}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


async def test_erased_text_cannot_be_read_back_out_of_the_sqlite_file(run_app, tmp_path):
    secret = "ZEBRA-QUOKKA-7731-unique-marker"
    async with run_app(None, FakeAdapter("local", reply=f"echo {secret}")) as (c, _):
        out = await chat(c, f"my note is {secret}")
        db = tmp_path / "test.db"
        if not db.exists():  # PostgreSQL run: the file check does not apply
            return
        assert secret.encode() in db.read_bytes()  # sanity: it is in the file before deletion
        assert (
            await c.delete(f"/v1/conversations/{out['conversation_id']}", headers=AUTH)
        ).status_code == 204
    assert secret.encode() not in db.read_bytes()  # overwritten, not just unlinked
    assert secret.encode() not in b"".join(p.read_bytes() for p in tmp_path.glob("test.db-*"))


async def test_erasing_removes_the_conversation_and_404s_afterwards(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        cid = (await chat(c, "hello"))["conversation_id"]
        assert (await c.delete(f"/v1/conversations/{cid}", headers=AUTH)).status_code == 204
        assert (await c.get(f"/v1/conversations/{cid}", headers=AUTH)).status_code == 404
        assert (await c.delete(f"/v1/conversations/{cid}", headers=AUTH)).status_code == 404
        assert (await c.get("/v1/conversations", headers=AUTH)).json() == []


async def test_suggestions_from_the_erased_chat_go_but_confirmed_notes_stay(run_app):
    local = FakeAdapter("local", reply='[{"kind":"semantic","text":"The operator keeps bees"}]')
    async with run_app(None, local, embedder=FakeEmbedder(), **MEM) as (c, app):
        cid = (await chat(c, "I keep bees"))["conversation_id"]
        await c.post(
            "/v1/memories", json={"content": "The operator likes jasmine tea"}, headers=AUTH
        )
        local.reply = '[{"kind":"semantic","text":"The operator keeps bees in Devon"}]'
        r = await c.post(f"/v1/conversations/{cid}/extract-memories", headers=AUTH)
        assert len(r.json()["proposals"]) == 1
        remembered = await chat(c, "remember that I live near the coast")
        assert (await c.get("/v1/memories", headers=AUTH)).json()["total"] == 3
        assert (await c.delete(f"/v1/conversations/{cid}", headers=AUTH)).status_code == 204
        async with app.state.services.sessionmaker() as s:
            rows = (await s.scalars(select(Memory))).all()
        assert sorted(m.status for m in rows) == ["confirmed", "confirmed"]  # the suggestion went
        assert all(m.source_conversation_id != cid for m in rows)
        assert remembered["memory"]["remembered"]["saved"] is True


async def test_a_new_conversation_starts_with_no_history_from_the_erased_one(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        first = await chat(c, "my secret codeword is mulberry")
        await c.delete(f"/v1/conversations/{first['conversation_id']}", headers=AUTH)
        await chat(c, "what was my codeword")
        sent = " ".join(m.content for m in local.requests[-1].messages)
        assert "mulberry" not in sent  # working memory of the erased chat is gone
