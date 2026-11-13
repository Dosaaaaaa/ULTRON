from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ultron_agent.broker import AgentError
from ultron_agent.config import ConfigError


async def run(agent, tool, args=None, *, approve=True, confirmation=None):
    from tests.conftest import approve_next

    if approve is not None and tool in {
        "files.create", "files.append", "files.copy", "files.move", "files.delete",
    }:  # fmt: skip
        approve_next(agent, approve=approve, confirmation=confirmation)
    return (await agent.call(tool, args or {}, source="test"))["result"]


# ------------------------------------------------------------------ system
async def test_system_tools_shape_and_no_identifying_data(agent):
    info = await run(agent, "system.info")
    assert info["cpu_threads"] and info["memory_total_bytes"] > 0 and info["os"] != ""
    assert "hostname" not in info and "user" not in info
    use = await run(agent, "system.usage")
    assert 0 <= use["cpu_percent"] <= 100 and 0 < use["memory_percent"] <= 100
    disks = (await run(agent, "system.storage"))["disks"]
    assert disks and all(d["used_bytes"] <= d["total_bytes"] for d in disks)


# ------------------------------------------------------------------ files: read only
async def test_list_hides_hidden_and_sensitive(agent, ws):
    for n in ("a.txt", ".hidden", ".env", "server.pem"):
        (ws / n).write_text("x")
    (ws / "sub").mkdir()
    names = {e["name"] for e in (await run(agent, "files.list", {"path": str(ws)}))["entries"]}
    assert names == {"a.txt", "sub"}
    names = {
        e["name"]
        for e in (await run(agent, "files.list", {"path": str(ws), "include_hidden": True}))[
            "entries"
        ]
    }
    assert names == {"a.txt", "sub", ".hidden"}  # sensitive names stay hidden regardless


async def test_search_by_substring_glob_depth_and_limits(agent, ws):
    (ws / "d1" / "d2").mkdir(parents=True)
    (ws / "report-2026.txt").write_text("x")
    (ws / "d1" / "Report-final.md").write_text("x")
    (ws / "d1" / "d2" / "deep-report.txt").write_text("x")
    (ws / "d1" / ".env").write_text("x")
    r = await run(agent, "files.search", {"path": str(ws), "pattern": "report"})
    assert {Path(x["path"]).name for x in r["results"]} == {
        "report-2026.txt", "Report-final.md", "deep-report.txt",
    }  # fmt: skip
    r = await run(agent, "files.search", {"path": str(ws), "pattern": "*.md"})
    assert [Path(x["path"]).name for x in r["results"]] == ["Report-final.md"]
    r = await run(agent, "files.search", {"path": str(ws), "pattern": "report", "max_depth": 1})
    assert "deep-report.txt" not in {Path(x["path"]).name for x in r["results"]}
    r = await run(agent, "files.search", {"path": str(ws), "pattern": "report", "max_results": 1})
    assert len(r["results"]) == 1 and r["truncated"]
    r = await run(
        agent, "files.search", {"path": str(ws), "pattern": "env", "include_hidden": True}
    )
    assert r["results"] == []  # protected names are never returned


async def test_search_does_not_follow_symlinks(agent, ws, short_tmp):
    out = short_tmp / "outside"
    out.mkdir()
    (out / "target-secret.txt").write_text("x")
    (ws / "link").symlink_to(out)
    r = await run(agent, "files.search", {"path": str(ws), "pattern": "target"})
    assert r["results"] == []


async def test_read_text_truncation_binary_and_scope(agent, ws, ro, short_tmp):
    (ro / "n.txt").write_text("héllo wörld")
    r = await run(agent, "files.read", {"path": str(ro / "n.txt")})
    assert r["text"] == "héllo wörld" and not r["truncated"]
    (ws / "big.txt").write_text("a" * 5000)
    r = await run(agent, "files.read", {"path": str(ws / "big.txt"), "max_bytes": 100})
    assert len(r["text"]) == 100 and r["truncated"] and r["size_bytes"] == 5000
    (ws / "b.bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(AgentError) as e:
        await run(agent, "files.read", {"path": str(ws / "b.bin")})
    assert e.value.code == "binary_file"
    (short_tmp / "outside.txt").write_text("x")
    with pytest.raises(AgentError) as e:
        await run(agent, "files.read", {"path": str(short_tmp / "outside.txt")})
    assert e.value.code == "denied"


async def test_read_refuses_fifo_without_blocking(agent, ws):
    os.mkfifo(ws / "pipe")
    with pytest.raises(AgentError) as e:
        await run(agent, "files.read", {"path": str(ws / "pipe")})
    assert e.value.code == "denied"


# ------------------------------------------------------------------ files: mutating
async def test_create_new_file_private_mode_and_never_overwrites(agent, ws):
    r = await run(agent, "files.create", {"path": str(ws / "n.txt"), "content": "hello"})
    assert r["bytes"] == 5 and (ws / "n.txt").read_text() == "hello"
    assert stat.S_IMODE((ws / "n.txt").stat().st_mode) == 0o600
    with pytest.raises(AgentError) as e:
        await run(agent, "files.create", {"path": str(ws / "n.txt"), "content": "other"})
    assert e.value.code == "exists" and (ws / "n.txt").read_text() == "hello"


async def test_create_refuses_symlink_leaf(agent, ws, short_tmp):
    target = short_tmp / "victim.txt"
    target.write_text("keep")
    (ws / "ln.txt").symlink_to(target)
    with pytest.raises(AgentError):
        await run(agent, "files.create", {"path": str(ws / "ln.txt"), "content": "pwn"})
    assert target.read_text() == "keep"


async def test_write_in_readonly_root_is_denied_even_if_approved(agent, ro):
    with pytest.raises(AgentError) as e:
        await run(agent, "files.create", {"path": str(ro / "x.txt"), "content": "x"})
    assert e.value.code == "denied" and not (ro / "x.txt").exists()


async def test_append(agent, ws):
    (ws / "log.txt").write_text("a")
    await run(agent, "files.append", {"path": str(ws / "log.txt"), "content": "b"})
    assert (ws / "log.txt").read_text() == "ab"


async def test_copy_and_move_never_overwrite(agent, ws, ro):
    (ro / "src.txt").write_text("S")
    (ws / "taken.txt").write_text("T")
    await run(agent, "files.copy", {"src": str(ro / "src.txt"), "dst": str(ws / "c.txt")})
    assert (ws / "c.txt").read_text() == "S" and (ro / "src.txt").exists()
    with pytest.raises(AgentError) as e:
        await run(agent, "files.copy", {"src": str(ro / "src.txt"), "dst": str(ws / "taken.txt")})
    assert e.value.code == "exists" and (ws / "taken.txt").read_text() == "T"
    await run(agent, "files.move", {"src": str(ws / "c.txt"), "dst": str(ws / "m.txt")})
    assert not (ws / "c.txt").exists() and (ws / "m.txt").read_text() == "S"
    (ws / "other.txt").write_text("O")
    with pytest.raises(AgentError) as e:
        await run(agent, "files.move", {"src": str(ws / "other.txt"), "dst": str(ws / "taken.txt")})
    assert e.value.code == "exists"
    assert (ws / "other.txt").read_text() == "O" and (ws / "taken.txt").read_text() == "T"


async def test_move_out_of_readonly_root_denied(agent, ws, ro):
    (ro / "f.txt").write_text("x")
    with pytest.raises(AgentError) as e:
        await run(agent, "files.move", {"src": str(ro / "f.txt"), "dst": str(ws / "f.txt")})
    assert e.value.code == "denied" and (ro / "f.txt").exists()


async def test_delete_goes_to_trash_with_trashinfo(agent, ws, cfg):
    (ws / "gone.txt").write_text("bye")
    r = await run(
        agent, "files.delete", {"path": str(ws / "gone.txt")}, confirmation="delete gone.txt"
    )
    assert not (ws / "gone.txt").exists()
    assert Path(r["trash_path"]).read_text() == "bye" and r["recoverable"]  # noqa: ASYNC240
    info = (cfg.trash_dir / "info" / "gone.txt.trashinfo").read_text()
    assert info.startswith("[Trash Info]") and "gone.txt" in info and "DeletionDate=" in info


async def test_delete_directory_not_supported(agent, ws):
    (ws / "dir").mkdir()
    (ws / "dir" / "f").write_text("x")
    with pytest.raises(AgentError):
        await run(agent, "files.delete", {"path": str(ws / "dir")}, confirmation="delete dir")
    assert (ws / "dir" / "f").exists()


# ------------------------------------------------------------------ applications
def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


async def test_launch_only_allowlisted_and_no_secrets_in_env(agent, cfg, short_tmp, monkeypatch):
    out = short_tmp / "env.txt"
    exe = _script(short_tmp / "fakeapp", f'env > "{out}"; sleep 2')
    cfg.add_app("fake", "Fake App", [str(exe)])
    monkeypatch.setenv("GEMINI_API_KEY", "SUPER-SECRET")
    monkeypatch.setenv("ULTRON_API_TOKEN", "TOKEN-VALUE")
    monkeypatch.setenv("DISPLAY", ":0")
    listing = (await run(agent, "app.list"))["apps"]
    assert listing == [{"id": "fake", "name": "Fake App", "installed": True}]
    r = await run(agent, "app.launch", {"app_id": "fake"})
    assert r["running"] and r["pid"] > 0
    for _ in range(40):
        if out.exists() and out.stat().st_size:
            break
        await __import__("asyncio").sleep(0.05)
    env = out.read_text()
    assert "SUPER-SECRET" not in env and "TOKEN-VALUE" not in env and "DISPLAY=:0" in env
    os.kill(r["pid"], 15)
    with pytest.raises(AgentError) as e:
        await run(agent, "app.launch", {"app_id": "not-listed"})
    assert e.value.code == "not_allowlisted"


async def test_launch_reports_crash_and_missing_program(agent, cfg, short_tmp):
    cfg.add_app("crash", "Crash", [str(_script(short_tmp / "crash", "exit 3"))])
    with pytest.raises(AgentError) as e:
        await run(agent, "app.launch", {"app_id": "crash"})
    assert e.value.code == "exited_immediately"
    cfg.add_app("gone", "Gone", ["definitely-not-installed-xyz"])
    with pytest.raises(AgentError) as e:
        await run(agent, "app.launch", {"app_id": "gone"})
    assert e.value.code == "not_installed"


@pytest.mark.parametrize("prog", ["bash", "/bin/sh", "python3", "env", "sudo", "curl"])
def test_shells_and_interpreters_cannot_be_allowlisted(cfg, prog):
    with pytest.raises(ConfigError):
        cfg.add_app("x", "X", [prog, "-c", "id"])


async def test_hand_edited_config_cannot_allowlist_a_shell(agent, cfg):
    from ultron_agent.config import AppEntry

    cfg.stored.apps["evil"] = AppEntry(name="evil", argv=["/bin/sh", "-c", "touch /tmp/pwned-ua"])
    with pytest.raises(AgentError):
        await run(agent, "app.launch", {"app_id": "evil"})
    assert not Path("/tmp/pwned-ua").exists()  # noqa: S108, ASYNC240


@pytest.mark.parametrize("bad", ["../etc", "a b", "a;b", "$(id)", ""])
async def test_app_id_must_be_plain(agent, bad):
    with pytest.raises(AgentError) as e:
        await run(agent, "app.launch", {"app_id": bad})
    assert e.value.code == "invalid_arguments"
