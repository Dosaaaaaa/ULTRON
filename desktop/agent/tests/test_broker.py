from __future__ import annotations

import asyncio
import json
import time

import pytest
from pydantic import BaseModel, ConfigDict

from tests.conftest import approve_next
from ultron_agent.audit import AuditLog
from ultron_agent.broker import MAX_PENDING, Agent, AgentError
from ultron_agent.levels import Level
from ultron_agent.registry import Registry, ToolSpec
from ultron_agent.tools import build_registry

SECRET = "TOP-SECRET-FILE-BODY-12345"


async def call(agent, tool, args=None, source="test"):
    return await agent.call(tool, args or {}, source=source)


# ------------------------------------------------------------------ rejection paths
async def test_unknown_tool_rejected_and_audited(agent):
    for name in ("shell.exec", "files.delete_all", "", None, 5):
        with pytest.raises(AgentError) as e:
            await call(agent, name)
        assert e.value.code == "unknown_tool"
    assert all(r["decision"] == "unknown_tool" for r in agent.audit.tail(10))


@pytest.mark.parametrize(
    "args",
    [{"path": "/x", "extra": 1}, {"path": 5}, {}, {"path": "/x", "max_bytes": 10**9}, "str", [1]],
)
async def test_invalid_arguments_rejected_without_echoing_values(agent, args):
    with pytest.raises(AgentError) as e:
        await call(agent, "files.read", args)
    assert e.value.code == "invalid_arguments"
    assert "1000000000" not in e.value.message


async def test_denied_path_never_reaches_approval(agent, short_tmp):
    q = agent.subscribe()
    with pytest.raises(AgentError) as e:
        await call(agent, "files.create", {"path": str(short_tmp / "x.txt"), "content": "x"})
    events = []
    while not q.empty():
        events.append(q.get_nowait()["event"])
    assert e.value.code == "denied" and "approval_requested" not in events and not agent.pending


# ------------------------------------------------------------------ approval workflow
async def test_p2_waits_for_approval_and_runs_only_after(agent, ws):
    path = ws / "n.txt"
    task = asyncio.create_task(call(agent, "files.create", {"path": str(path), "content": "hi"}))
    await asyncio.sleep(0.2)
    assert not path.exists() and len(agent.pending) == 1 and not task.done()
    ap = next(iter(agent.pending.values()))
    assert ap.level is Level.P2 and str(path) in ap.summary
    agent.decide(ap.id, True, None, "human")
    out = await task
    assert out["decision"] == "approved" and path.read_text() == "hi"
    assert not agent.pending


async def test_denial_means_nothing_happens(agent, ws):
    approve_next(agent, approve=False)
    with pytest.raises(AgentError) as e:
        await call(agent, "files.create", {"path": str(ws / "n.txt"), "content": "hi"})
    assert e.value.code == "denied" and not (ws / "n.txt").exists()


async def test_no_decision_times_out_as_denial(agent, ws, cfg):
    cfg.stored.approval_timeout_s = 0.3
    t0 = time.monotonic()
    with pytest.raises(AgentError) as e:
        await call(agent, "files.create", {"path": str(ws / "n.txt"), "content": "hi"})
    assert e.value.code == "approval_timeout" and time.monotonic() - t0 < 2
    assert not (ws / "n.txt").exists() and not agent.pending


async def test_p3_requires_exact_typed_confirmation(agent, ws):
    (ws / "f.txt").write_text("x")
    task = asyncio.create_task(call(agent, "files.delete", {"path": str(ws / "f.txt")}))
    await asyncio.sleep(0.2)
    ap = next(iter(agent.pending.values()))
    assert ap.confirm_text == "delete f.txt"
    for wrong in (None, "", "yes", "delete g.txt", "DELETE f.txt"):
        with pytest.raises(AgentError) as e:
            agent.decide(ap.id, True, wrong, "human")
        assert e.value.code == "confirmation_mismatch"
    assert (ws / "f.txt").exists() and ap.id in agent.pending  # still waiting, still intact
    agent.decide(ap.id, True, "delete f.txt", "human")
    await task
    assert not (ws / "f.txt").exists()


async def test_p3_denial_needs_no_confirmation_text(agent, ws):
    (ws / "f.txt").write_text("x")
    approve_next(agent, approve=False)
    with pytest.raises(AgentError):
        await call(agent, "files.delete", {"path": str(ws / "f.txt")})
    assert (ws / "f.txt").exists()


async def test_decide_is_one_shot_and_unknown_ids_fail(agent, ws):
    with pytest.raises(AgentError) as e:
        agent.decide("nope", True, None, "human")
    assert e.value.code == "unknown_approval"
    task = asyncio.create_task(call(agent, "files.create", {"path": str(ws / "a"), "content": "1"}))
    await asyncio.sleep(0.1)
    ap = next(iter(agent.pending.values()))
    agent.decide(ap.id, True, None, "human")
    await task
    with pytest.raises(AgentError):
        agent.decide(ap.id, True, None, "human")  # replay of the same approval


async def test_pending_approvals_are_capped(agent, ws):
    tasks = [
        asyncio.create_task(call(agent, "files.create", {"path": str(ws / f"{i}"), "content": "x"}))
        for i in range(MAX_PENDING)
    ]
    await asyncio.sleep(0.2)
    assert len(agent.pending) == MAX_PENDING
    with pytest.raises(AgentError) as e:
        await call(agent, "files.create", {"path": str(ws / "overflow"), "content": "x"})
    assert e.value.code == "too_many_pending"
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert not agent.pending


async def test_policy_is_rechecked_after_approval(agent, ws, cfg):
    task = asyncio.create_task(
        call(agent, "files.create", {"path": str(ws / "n.txt"), "content": "x"})
    )
    await asyncio.sleep(0.2)
    cfg.remove_root(str(ws))  # operator revokes the folder while the dialog is open
    agent.decide(next(iter(agent.pending)), True, None, "human")
    with pytest.raises(AgentError) as e:
        await task
    assert e.value.code == "denied" and not (ws / "n.txt").exists()


# ------------------------------------------------------------------ execution safety
def _registry_with(extra: ToolSpec) -> Registry:
    reg = build_registry()
    reg.register(extra)
    return reg


class Nothing(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _spec(name, handler, level=Level.P0, timeout=0.3):
    return ToolSpec(
        name=name, description="", input_model=Nothing, level=level, timeout_s=timeout,
        handler=handler, describe=lambda a: name,
    )  # fmt: skip


async def test_slow_tool_times_out(cfg):
    agent = Agent(cfg, _registry_with(_spec("t.slow", lambda a, c: time.sleep(1.5))))
    t0 = time.monotonic()
    with pytest.raises(AgentError) as e:
        await call(agent, "t.slow")
    assert e.value.code == "timeout" and time.monotonic() - t0 < 1.4


async def test_unexpected_exception_does_not_leak_message(cfg):
    def boom(a, c):
        raise RuntimeError("leaked /home/user/secret/path password=hunter2")

    agent = Agent(cfg, _registry_with(_spec("t.boom", boom)))
    with pytest.raises(AgentError) as e:
        await call(agent, "t.boom")
    assert e.value.code == "failed" and "hunter2" not in e.value.message + e.value.code
    assert "hunter2" not in json.dumps(agent.audit.tail(5))


async def test_p3_tool_must_declare_confirmation_text():
    reg = Registry()
    with pytest.raises(ValueError, match="confirm_text"):
        reg.register(_spec("t.bad", lambda a, c: None, level=Level.P3))


async def test_p4_tools_blocked_by_default(cfg):
    spec = _spec("t.p4", lambda a, c: "ran", level=Level.P4)
    spec = ToolSpec(**{**spec.__dict__, "confirm_text": lambda a: "ok"})
    agent = Agent(cfg, _registry_with(spec))
    with pytest.raises(AgentError) as e:
        await call(agent, "t.p4")
    assert e.value.code == "denied"


# ------------------------------------------------------------------ audit
async def test_audit_records_every_outcome_without_contents(agent, ws):
    approve_next(agent)
    await call(agent, "files.create", {"path": str(ws / "s.txt"), "content": SECRET})
    approve_next(agent, approve=False)
    with pytest.raises(AgentError):
        await call(agent, "files.create", {"path": str(ws / "s2.txt"), "content": SECRET})
    with pytest.raises(AgentError):
        await call(agent, "nope")
    await call(agent, "files.read", {"path": str(ws / "s.txt")})
    raw = agent.audit.path.read_text()
    assert SECRET not in raw  # neither written content nor read content is ever logged
    recs = agent.audit.tail(10)
    assert [(r["decision"], r["outcome"]) for r in recs] == [
        ("approved", "intent"),  # written BEFORE the file is created
        ("approved", "ok"),
        ("refused_by_operator", "error"),
        ("unknown_tool", "error"),
        ("auto", "ok"),
    ]
    created = recs[1]
    assert (
        created["args"]["content_bytes"] == len(SECRET)
        and len(created["args"]["content_sha256"]) == 64
    )
    assert created["approver"] == "test-human" and created["outcome"] == "ok"
    assert recs[2]["outcome"] == "error" and recs[2]["error_code"] == "denied"
    assert agent.audit.verify().ok


async def test_audit_tamper_detection(agent):
    for _ in range(4):
        await call(agent, "system.info")
    path = agent.audit.path
    good = path.read_text()
    assert agent.audit.verify().records == 4
    lines = good.splitlines()
    path.write_text(
        "\n".join([lines[0], lines[1].replace('"auto"', '"approved"'), *lines[2:]]) + "\n"
    )
    v = agent.audit.verify()
    assert not v.ok and v.first_bad_line == 2 and "modified" in v.reason
    path.write_text("\n".join([lines[0], lines[2], lines[3]]) + "\n")  # delete a middle record
    v = agent.audit.verify()
    assert not v.ok and v.first_bad_line == 2 and "chain" in v.reason
    path.write_text(good)
    assert agent.audit.verify().ok


async def test_audit_file_is_private_and_chain_survives_restart(cfg):
    a1 = Agent(cfg)
    await call(a1, "system.info")
    assert oct(cfg.audit_file.stat().st_mode & 0o777) == "0o600"
    a2 = Agent(cfg, audit=AuditLog(cfg.audit_file))
    await call(a2, "system.info")
    v = a2.audit.verify()
    assert v.ok and v.records == 2


async def test_unwritable_audit_log_blocks_mutations_but_not_reads(agent, ws, monkeypatch):
    def broken(**_kw):
        raise OSError("disk full")

    monkeypatch.setattr(agent.audit, "write", broken)
    approve_next(agent)
    with pytest.raises(AgentError) as e:
        await call(agent, "files.create", {"path": str(ws / "unaudited.txt"), "content": "x"})
    assert e.value.code == "audit_unavailable" and not (ws / "unaudited.txt").exists()
    assert (await call(agent, "system.info"))["ok"]  # read-only calls are still served


# ------------------------------------------------------------------ review findings
async def test_the_approval_dialog_names_the_file_that_will_really_be_changed(agent, ws):
    # A symlink inside the approved root points at another file in the root. The operator must be
    # told the file the action will really touch, not only the name they were handed.
    target = ws / "important.db"
    target.write_text("keep me")
    (ws / "notes.txt").symlink_to(target)
    task = asyncio.create_task(
        call(agent, "files.append", {"path": str(ws / "notes.txt"), "content": "x"})
    )
    await asyncio.sleep(0.2)
    ap = next(iter(agent.pending.values()))
    assert str(target) in ap.summary and str(target) in json.dumps(ap.public())
    agent.decide(ap.id, False, None, "human")
    with pytest.raises(AgentError):
        await task
    assert target.read_text() == "keep me"


async def test_a_plain_path_adds_nothing_to_the_summary(agent, ws):
    task = asyncio.create_task(
        call(agent, "files.create", {"path": str(ws / "n.txt"), "content": "hi"})
    )
    await asyncio.sleep(0.2)
    ap = next(iter(agent.pending.values()))
    assert "Acts on" not in ap.summary
    agent.decide(ap.id, False, None, "human")
    with pytest.raises(AgentError):
        await task


async def test_time_spent_waiting_for_the_lock_does_not_count_against_the_timeout(cfg):
    import threading

    gate, started = threading.Event(), threading.Event()

    def slow(a, ctx):
        started.set()
        gate.wait(5)
        return {"ok": True}

    reg = _registry_with(_spec("t.slow", slow, level=Level.P2, timeout=5.0))
    reg.register(_spec("t.quick", lambda a, c: {"ok": True}, level=Level.P2, timeout=0.3))
    agent = Agent(cfg, reg)
    approve_next(agent, approve=True)
    first = asyncio.create_task(call(agent, "t.slow"))
    await asyncio.to_thread(started.wait, 3)
    approve_next(agent, approve=True)
    second = asyncio.create_task(call(agent, "t.quick"))  # queued behind the slow one
    await asyncio.sleep(1.6)  # longer than quick's own 0.3 s timeout (+1 s grace)
    gate.set()
    assert (await first)["result"] == {"ok": True}
    assert (await second)["result"] == {"ok": True}  # was "timeout" before the fix
