"""F2: privacy is a property of the conversation, not of one request."""

from __future__ import annotations

import asyncio
import sqlite3

from alembic import command

from app.db.session import alembic_config
from tests.conftest import AUTH
from tests.fakes import FakeAdapter, down

COMPLEX = "Analyze the strategic tradeoffs of this plan in detail."


async def chat(client, message, **extra):
    return await client.post("/v1/chat", json={"message": message, **extra}, headers=AUTH)


async def test_follow_up_default_turn_is_forced_local_and_cloud_sees_nothing(run_app):
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    secret = "-".join(["vault", "code", "7319"])
    async with run_app(cloud, local) as (c, _):
        first = (await chat(c, f"private note: {secret}", privacy="local_only")).json()
        assert first["privacy_locked"] is True
        cid = first["conversation_id"]

        second = await chat(c, COMPLEX, conversation_id=cid)  # default privacy, complex => cloud
        b = second.json()

    assert second.status_code == 200
    assert cloud.requests == []  # not one byte reached the cloud adapter
    assert b["engine"]["tier"] == "local"
    assert b["privacy_locked"] is True and b["route"]["privacy"] == "local_only"
    assert "local-only" in b["notice"] and "earlier turn" in b["notice"]
    # the private turn was part of the history the local model received
    sent = " ".join(m.content for m in local.requests[-1].messages)
    assert secret in sent


async def test_lock_applies_from_the_moment_it_is_set(run_app):
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    async with run_app(cloud, local) as (c, _):
        a = (await chat(c, COMPLEX)).json()  # default privacy: cloud is allowed
        assert a["engine"]["tier"] == "cloud" and a["privacy_locked"] is False
        cid = a["conversation_id"]
        cloud_calls = len(cloud.requests)

        b = (
            await chat(c, "now something private", conversation_id=cid, privacy="local_only")
        ).json()
        assert b["privacy_locked"] is True
        c3 = (await chat(c, COMPLEX, conversation_id=cid)).json()

    assert c3["engine"]["tier"] == "local"
    assert len(cloud.requests) == cloud_calls  # no cloud call after the lock


async def test_explicit_local_only_on_locked_conversation_has_no_escalation_notice(run_app):
    async with run_app(FakeAdapter("cloud"), FakeAdapter("local")) as (c, _):
        cid = (await chat(c, "a", privacy="local_only")).json()["conversation_id"]
        b = (await chat(c, "b", conversation_id=cid, privacy="local_only")).json()
    assert b["notice"] is None and b["privacy_locked"] is True


async def test_locked_conversation_with_local_down_is_503_not_cloud(run_app):
    cloud = FakeAdapter("cloud")
    local = FakeAdapter("local")
    async with run_app(cloud, local) as (c, _):
        cid = (await chat(c, "private", privacy="local_only")).json()["conversation_id"]
        local.error = down("local").error
        r = await chat(c, COMPLEX, conversation_id=cid)
    assert r.status_code == 503 and cloud.requests == []


async def test_failed_local_only_request_locks_nothing_and_saves_nothing(run_app):
    async with run_app(FakeAdapter("cloud"), down("local")) as (c, _):
        r = await chat(c, "private", privacy="local_only")
        assert r.status_code == 503
        assert (await c.get("/v1/conversations", headers=AUTH)).json() == []


async def test_new_default_conversation_is_not_locked_and_flags_are_exposed(run_app):
    async with run_app(FakeAdapter("cloud"), FakeAdapter("local")) as (c, _):
        open_id = (await chat(c, COMPLEX)).json()["conversation_id"]
        locked_id = (await chat(c, "x", privacy="local_only")).json()["conversation_id"]
        listed = {
            x["id"]: x["local_only"]
            for x in (await c.get("/v1/conversations", headers=AUTH)).json()
        }
        detail = (await c.get(f"/v1/conversations/{locked_id}", headers=AUTH)).json()
    assert listed == {open_id: False, locked_id: True}
    assert detail["local_only"] is True


async def test_lock_survives_restart(run_app):
    cloud = FakeAdapter("cloud")
    async with run_app(cloud, FakeAdapter("local")) as (c, _):
        cid = (await chat(c, "private", privacy="local_only")).json()["conversation_id"]
    async with run_app(cloud, FakeAdapter("local")) as (c, _):
        b = (await chat(c, COMPLEX, conversation_id=cid)).json()
    assert b["engine"]["tier"] == "local" and cloud.requests == []


async def test_concurrent_requests_cannot_slip_a_cloud_call_between_lock_and_default(run_app):
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    async with run_app(cloud, local) as (c, _):
        cid = (await chat(c, "seed", privacy="local_only")).json()["conversation_id"]
        results = await asyncio.gather(*(chat(c, COMPLEX, conversation_id=cid) for _ in range(5)))
    assert all(r.status_code == 200 and r.json()["engine"]["tier"] == "local" for r in results)
    assert cloud.requests == []


async def test_migration_0002_backfills_locks_from_stored_route_privacy(tmp_path):
    """Conversations written before the column existed are locked if any turn was local_only."""
    db = tmp_path / "bf.db"
    url = f"sqlite+aiosqlite:///{db}"
    cfg = alembic_config(url)
    await asyncio.to_thread(command.upgrade, cfg, "0001")

    con = sqlite3.connect(db)
    now = "2026-01-01 00:00:00.000000"
    for cid in ("a" * 36, "b" * 36, "c" * 36):
        con.execute(
            "insert into conversations(id,title,created_at,updated_at) values (?,?,?,?)",
            (cid, "t", now, now),
        )
    rows = [
        ("a" * 36, "user", "hi", None),
        ("a" * 36, "assistant", "hello", '{"privacy": "default"}'),
        ("a" * 36, "assistant", "private", '{"privacy": "local_only"}'),  # => locked
        ("b" * 36, "assistant", "plain", '{"privacy": "default"}'),
        ("c" * 36, "user", "no route at all", None),
    ]
    for cid, role, content, route in rows:
        con.execute(
            "insert into messages(conversation_id,role,content,created_at,route) "
            "values (?,?,?,?,?)",
            (cid, role, content, now, route),
        )
    con.commit()
    con.close()

    await asyncio.to_thread(command.upgrade, cfg, "head")
    flags = dict(sqlite3.connect(db).execute("select id, local_only from conversations"))
    assert flags == {"a" * 36: 1, "b" * 36: 0, "c" * 36: 0}
