from __future__ import annotations

import pytest

from app.models.base import ProviderRejected
from tests.conftest import AUTH, TOKEN, make_settings
from tests.fakes import FakeAdapter, down


async def post_chat(client, message="hello", **extra):
    return await client.post("/v1/chat", json={"message": message, **extra}, headers=AUTH)


# ---- health ------------------------------------------------------------------
async def test_liveness_is_public_and_minimal(run_app):
    async with run_app(FakeAdapter("cloud"), FakeAdapter("local")) as (c, _):
        r = await c.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert "X-Request-ID" in r.headers


async def test_readiness_requires_auth_and_reports_engines(run_app):
    async with run_app(FakeAdapter("cloud"), FakeAdapter("local")) as (c, _):
        assert (await c.get("/v1/health/ready")).status_code == 401
        body = (await c.get("/v1/health/ready", headers=AUTH)).json()
        assert body["status"] == "ok" and body["database"] == {"ok": True}
        assert {e["tier"] for e in body["engines"]} == {"cloud", "local"}
        assert body["personality"]["version"] == 1
        assert TOKEN not in str(body)


async def test_readiness_degraded_when_all_probed_engines_down(run_app):
    async with run_app(None, down("local")) as (c, _):
        body = (await c.get("/v1/health/ready", headers=AUTH)).json()
        assert body["status"] == "degraded"


# ---- auth ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong" + "x" * 30},
        {"Authorization": TOKEN},
        {"Authorization": f"Basic {TOKEN}"},
    ],
)
async def test_chat_rejects_bad_credentials(run_app, headers):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        r = await c.post("/v1/chat", json={"message": "hi"}, headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert r.json()["error"]["code"] == "unauthorized"
    assert local.requests == []  # the model was never called


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/conversations"),
        ("GET", "/v1/conversations/" + "0" * 36),
        ("DELETE", "/v1/conversations/" + "0" * 36),
        ("GET", "/v1/usage"),
    ],
)
async def test_every_v1_route_requires_auth(run_app, method, path):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        assert (await c.request(method, path)).status_code == 401


# ---- chat ----------------------------------------------------------------------
async def test_chat_creates_conversation_and_reports_engine(run_app):
    local = FakeAdapter("local", model="qwen3:4b", provider="ollama", reply="Acknowledged.")
    async with run_app(None, local) as (c, _):
        r = await post_chat(c, "hi")
    assert r.status_code == 200
    b = r.json()
    assert b["reply"] == "Acknowledged."
    assert b["engine"] == {"provider": "ollama", "model": "qwen3:4b", "tier": "local"}
    assert b["route"]["attempts"][0]["ok"] is True and b["personality_version"] == 1
    assert b["usage"] == {"latency_ms": 7, "prompt_tokens": 11, "completion_tokens": 5}
    assert len(b["conversation_id"]) == 36 and b["notice"] is None


async def test_personality_prompt_reaches_the_model(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        await post_chat(c, "hi")
    system = local.requests[0].messages[0]
    assert system.role == "system" and "You are ULTRON" in system.content
    assert "NO tools" in system.content


async def test_history_is_sent_on_continuation(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        cid = (await post_chat(c, "first")).json()["conversation_id"]
        await post_chat(c, "second", conversation_id=cid)
    roles = [(m.role, m.content) for m in local.requests[1].messages[1:]]
    assert roles == [("user", "first"), ("assistant", "reply from local-model"), ("user", "second")]


async def test_context_window_is_capped(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local, max_context_messages=4) as (c, _):
        cid = (await post_chat(c, "m0")).json()["conversation_id"]
        for i in range(1, 5):
            await post_chat(c, f"m{i}", conversation_id=cid)
    sent = local.requests[-1].messages[1:]  # minus system
    assert len(sent) == 5  # 4 history + the new user message
    assert sent[-1].content == "m4" and sent[0].content != "m0"


async def test_conversation_persists_across_app_restart(run_app):
    local = FakeAdapter("local", reply="remembered")
    async with run_app(None, local) as (c, _):
        cid = (await post_chat(c, "persist me")).json()["conversation_id"]
    # new app instance, same SQLite file (tmp_path is shared by the fixture)
    async with run_app(None, FakeAdapter("local")) as (c, _):
        detail = (await c.get(f"/v1/conversations/{cid}", headers=AUTH)).json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "persist me"),
        ("assistant", "remembered"),
    ]
    assert detail["messages"][1]["engine"]["tier"] == "local"
    assert detail["messages"][1]["personality_version"] == 1
    assert detail["messages"][1]["route"]["policy"] == "auto"
    assert detail["title"] == "persist me"


async def test_list_and_delete_conversations(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        a = (await post_chat(c, "alpha")).json()["conversation_id"]
        b = (await post_chat(c, "beta")).json()["conversation_id"]
        listed = (await c.get("/v1/conversations", headers=AUTH)).json()
        assert [x["id"] for x in listed] == [b, a]  # most recently updated first
        assert listed[0]["message_count"] == 2

        assert (await c.delete(f"/v1/conversations/{a}", headers=AUTH)).status_code == 204
        assert (await c.get(f"/v1/conversations/{a}", headers=AUTH)).status_code == 404
        assert (await c.delete(f"/v1/conversations/{a}", headers=AUTH)).status_code == 404
        assert [x["id"] for x in (await c.get("/v1/conversations", headers=AUTH)).json()] == [b]


async def test_deleting_a_conversation_deletes_its_messages(run_app):
    from sqlalchemy import func, select

    from app.db.models import Message

    async with run_app(None, FakeAdapter("local")) as (c, app):
        cid = (await post_chat(c, "x")).json()["conversation_id"]
        await c.delete(f"/v1/conversations/{cid}", headers=AUTH)
        async with app.state.services.sessionmaker() as s:
            assert await s.scalar(select(func.count(Message.id))) == 0


async def test_unknown_conversation_is_404_and_model_not_called(run_app):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        r = await post_chat(c, "hi", conversation_id="0" * 36)
    assert r.status_code == 404 and local.requests == []


# ---- validation ------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 8001},
        {"message": "hi", "privacy": "public"},
        {"message": "hi", "conversation_id": "not-a-uuid"},
        {"message": 5},
    ],
)
async def test_chat_validates_input(run_app, payload):
    local = FakeAdapter("local")
    async with run_app(None, local) as (c, _):
        r = await c.post("/v1/chat", json=payload, headers=AUTH)
    assert r.status_code == 422 and local.requests == []


# ---- failure / fallback ------------------------------------------------------------
async def test_cloud_failure_is_reported_honestly_in_the_response(run_app):
    async with run_app(down("cloud"), FakeAdapter("local", model="qwen3:4b")) as (c, _):
        b = (await post_chat(c, "Analyze the strategic tradeoffs of this plan")).json()
    assert b["engine"]["tier"] == "local"
    assert b["route"]["fallback"] and b["route"]["degraded"]
    assert [a["ok"] for a in b["route"]["attempts"]] == [False, True]
    assert "less capable" in b["notice"]


async def test_all_models_down_is_503_and_saves_nothing(run_app):
    async with run_app(down("cloud"), down("local")) as (c, _):
        r = await post_chat(c, "Analyze this plan")
        assert r.status_code == 503
        err = r.json()["error"]
        assert err["code"] == "models_unavailable" and "Nothing was saved" in err["message"]
        assert [a["error"] for a in err["attempts"]] == ["unavailable", "unavailable"]
        assert (await c.get("/v1/conversations", headers=AUTH)).json() == []


async def test_local_only_never_uses_cloud_even_when_local_is_down(run_app):
    cloud = FakeAdapter("cloud")
    async with run_app(cloud, down("local")) as (c, _):
        r = await post_chat(c, "Analyze this confidential plan", privacy="local_only")
    assert r.status_code == 503 and cloud.requests == []


async def test_local_only_request_is_answered_locally_even_if_complex(run_app):
    cloud, local = FakeAdapter("cloud"), FakeAdapter("local")
    async with run_app(cloud, local) as (c, _):
        b = (await post_chat(c, "Analyze this confidential plan", privacy="local_only")).json()
    assert b["engine"]["tier"] == "local" and cloud.requests == []
    assert b["route"]["privacy"] == "local_only" and b["route"]["degraded"]
    assert "less capable" in b["notice"]


async def test_non_fallback_errors_like_bad_credentials_also_fall_back(run_app):
    cloud = FakeAdapter("cloud", error=ProviderRejected("401"))
    async with run_app(cloud, FakeAdapter("local")) as (c, _):
        b = (await post_chat(c, "Analyze this plan")).json()
    assert b["route"]["attempts"][0]["error_kind"] == "rejected" and b["engine"]["tier"] == "local"


async def test_usage_endpoint_reports_counters(run_app):
    async with run_app(None, FakeAdapter("local")) as (c, _):
        await post_chat(c, "hi")
        u = (await c.get("/v1/usage", headers=AUTH)).json()
    assert u["fake-local/local-model"]["requests"] == 1


# ---- rate limiting / errors / logging ------------------------------------------------
async def test_rate_limit_returns_429_with_retry_after(run_app):
    async with run_app(None, FakeAdapter("local"), rate_limit_per_minute=2) as (c, _):
        assert (await post_chat(c)).status_code == 200
        assert (await post_chat(c)).status_code == 200
        r = await post_chat(c)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert r.json()["error"]["code"] == "rate_limited"


async def test_unauthenticated_requests_do_not_consume_rate_limit(run_app):
    async with run_app(None, FakeAdapter("local"), rate_limit_per_minute=1) as (c, _):
        for _ in range(5):
            assert (await c.post("/v1/chat", json={"message": "x"})).status_code == 401
        assert (await post_chat(c)).status_code == 200


async def test_unhandled_error_hides_internals(run_app):
    class Boom(FakeAdapter):
        async def complete(self, request):
            raise RuntimeError("secret internal detail /home/binu/x")

    async with run_app(None, Boom("local")) as (c, app):
        # ASGITransport re-raises server errors by default; assert on the handler's own response.
        c._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        r = await post_chat(c)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text and "Traceback" not in r.text


async def test_logs_never_contain_tokens_or_message_bodies(run_app, capsys):
    # The app installs its own JSON handler on stderr, so read what it really wrote.
    async with run_app(None, FakeAdapter("local", reply="REPLY-BODY-XYZ"), log_level="DEBUG") as (
        c,
        _,
    ):
        await post_chat(c, "USER-BODY-XYZ")
        await c.post(
            "/v1/chat", json={"message": "x"}, headers={"Authorization": "Bearer nope" + "n" * 20}
        )
    logged = capsys.readouterr().err
    assert '"msg": "request"' in logged  # request logging is on, so the checks below mean something
    assert TOKEN not in logged and "nope" + "n" * 20 not in logged
    assert "USER-BODY-XYZ" not in logged and "REPLY-BODY-XYZ" not in logged


def test_settings_factory_for_tests_do_not_leak_between_apps(tmp_path):
    a, b = make_settings(tmp_path), make_settings(tmp_path, routing_policy="local_only")
    assert a.routing_policy == "auto" and b.routing_policy == "local_only"


async def test_unhandled_error_log_never_contains_exception_message_or_body(run_app, capsys):
    """F3: libraries embed request data in exception messages; the logger must not print them."""
    # Built at runtime: logged stack frames include source lines, so literals here would
    # match the log without any real leak.
    body = "-".join(["priv", "body", "f3"])
    marker = "-".join(["driver", "marker"])

    class Boom(FakeAdapter):
        async def complete(self, request):
            raise RuntimeError(f"{marker} params ({request.messages[-1].content!r},)")

    async with run_app(None, Boom("local")) as (c, _):
        c._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        r = await post_chat(c, body)
    assert r.status_code == 500
    err = capsys.readouterr().err
    assert '"msg": "unhandled_error"' in err and "builtins.RuntimeError" in err
    assert body not in err and marker not in err
    assert body not in r.text


async def test_engine_hides_sql_parameters(run_app):
    async with run_app(None, FakeAdapter("local")) as (_, app):
        assert app.state.services.engine.sync_engine.hide_parameters is True


async def test_first_run_creates_a_missing_database_directory(tmp_path):
    from app.main import create_app  # noqa: PLC0415

    db = tmp_path / "does" / "not" / "exist" / "u.db"
    settings = make_settings(tmp_path, database_url=f"sqlite+aiosqlite:///{db}")
    app = create_app(settings, local=FakeAdapter("local"))
    async with app.router.lifespan_context(app):
        pass
    assert db.exists()
