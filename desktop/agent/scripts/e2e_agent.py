"""Live end-to-end check of the REAL agent process (not the in-process test fixtures).

Starts `python -m ultron_agent serve` with a throwaway XDG config/state/data/runtime tree, approves a
temp folder through the real CLI, then drives the real sockets. Nothing outside the temp tree is
touched (config, audit log and Trash are all redirected). Prints a JSON report.

    .venv/bin/python scripts/e2e_agent.py OUT.json
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ultron_agent.client import AgentCallError, AgentClient

PY = sys.executable


async def main(out: Path) -> int:
    base = Path(tempfile.mkdtemp(prefix="uae"))
    ws, ro, outside = base / "ws", base / "ro", base / "outside"
    for d in (ws, ro, outside):
        d.mkdir()
    (ro / "notes.txt").write_text("read me")
    (outside / "secret.txt").write_text("outside")
    (ws / ".env").write_text("TOKEN=abc")
    env = {
        **os.environ,
        "HOME": str(base / "home"),
        "XDG_CONFIG_HOME": str(base / "cfg"),
        "XDG_STATE_HOME": str(base / "state"),
        "XDG_DATA_HOME": str(base / "data"),
        "XDG_RUNTIME_DIR": str(base / "run"),
    }
    (base / "home").mkdir()
    (base / "run").mkdir(mode=0o700)

    def cli(*a: str) -> str:
        return subprocess.run(  # noqa: S603
            [PY, "-m", "ultron_agent", *a], env=env, capture_output=True, text=True, check=False
        ).stdout.strip()

    report: dict = {"checks": {}}
    chk = report["checks"]
    chk["roots_add_rw"] = cli("roots", "add", str(ws), "--write")
    chk["roots_add_ro"] = cli("roots", "add", str(ro))
    chk["roots_reject_home"] = subprocess.run(  # noqa: S603
        [PY, "-m", "ultron_agent", "roots", "add", str(base / "home")],
        env=env, capture_output=True, text=True, check=False,
    ).stderr.strip()  # fmt: skip
    chk["apps_reject_shell"] = subprocess.run(  # noqa: S603
        [PY, "-m", "ultron_agent", "apps", "add", "x", "--cmdline", "bash -c id"],
        env=env, capture_output=True, text=True, check=False,
    ).stderr.strip()  # fmt: skip
    server = subprocess.Popen(  # noqa: S603
        [
            PY,
            os.environ.get("E2E_SERVER_SCRIPT", "-m"),
            *([] if os.environ.get("E2E_SERVER_SCRIPT") else ["ultron_agent", "serve"]),
        ],
        env={**env, "PYTHONFAULTHANDLER": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    run = base / "run" / "ultron"
    try:
        for _ in range(60):
            if (run / "console.sock").exists():
                break
            await asyncio.sleep(0.1)
        async with (
            AgentClient(run / "request.sock") as req,
            AgentClient(run / "console.sock") as ui,
        ):
            chk["status"] = await req.simple("status")
            chk["system_info_keys"] = sorted((await req.call("system.info")).keys())
            chk["system_usage"] = await req.call("system.usage")
            chk["storage_disks"] = len((await req.call("system.storage"))["disks"])
            chk["read_ro_file"] = (await req.call("files.read", {"path": str(ro / "notes.txt")}))[
                "text"
            ]
            chk["search"] = [Path(r["path"]).name for r in
                             (await req.call("files.search", {"path": str(ro), "pattern": "note"}))["results"]]  # fmt: skip

            async def refused(tool, args):
                try:
                    await req.call(tool, args)
                    return "NOT REFUSED"
                except AgentCallError as e:
                    return f"{e.code}"

            chk["read_outside_root"] = await refused(
                "files.read", {"path": str(outside / "secret.txt")}
            )
            chk["read_dotenv_in_root"] = await refused("files.read", {"path": str(ws / ".env")})
            chk["traversal"] = await refused("files.read", {"path": f"{ro}/../outside/secret.txt"})
            chk["write_ro_root"] = await refused(
                "files.create", {"path": str(ro / "x"), "content": "x"}
            )
            chk["unknown_tool"] = await refused("shell.exec", {"cmd": "id"})
            chk["extra_args"] = await refused("system.info", {"cmd": "id"})
            try:
                await req.simple("decide", approval_id="x", approve=True)
                chk["self_approve_blocked"] = "NOT REFUSED"
            except AgentCallError as e:
                chk["self_approve_blocked"] = e.code

            async def with_human(tool, args, approve=True, confirmation=None):
                task = asyncio.create_task(req.call(tool, args))
                while True:
                    ev = await asyncio.wait_for(ui.events.get(), 5)
                    if ev["event"] == "approval_requested":
                        break
                await ui.simple(
                    "decide", approval_id=ev["approval"]["id"], approve=approve,
                    confirmation=confirmation,
                )  # fmt: skip
                try:
                    return {"ok": await task, "approval": ev["approval"]["summary"]}
                except AgentCallError as e:
                    return {"error": e.code}

            chk["create_approved"] = await with_human(
                "files.create", {"path": str(ws / "hello.txt"), "content": "hello from ULTRON"}
            )
            chk["create_file_exists"] = (ws / "hello.txt").read_text()
            chk["create_denied"] = await with_human(
                "files.create", {"path": str(ws / "no.txt"), "content": "x"}, approve=False
            )
            chk["denied_file_absent"] = not (ws / "no.txt").exists()
            chk["delete_wrong_confirmation"] = None
            task = asyncio.create_task(req.call("files.delete", {"path": str(ws / "hello.txt")}))
            while (ev := await asyncio.wait_for(ui.events.get(), 5))[
                "event"
            ] != "approval_requested":
                pass
            try:
                await ui.simple(
                    "decide", approval_id=ev["approval"]["id"], approve=True, confirmation="yes"
                )
            except AgentCallError as e:
                chk["delete_wrong_confirmation"] = e.code
            chk["file_still_there_after_wrong_confirmation"] = (ws / "hello.txt").exists()
            await ui.simple(
                "decide",
                approval_id=ev["approval"]["id"],
                approve=True,
                confirmation="delete hello.txt",
            )
            chk["delete_result"] = await task
            chk["trash_has_file"] = (base / "data/Trash/files/hello.txt").read_text()
            chk["audit_verify"] = await ui.simple("audit_verify")
            tail = await ui.simple("audit_tail", n=100)
            chk["audit_decisions"] = [r["decision"] for r in tail]
            raw = (base / "state/ultron/audit.jsonl").read_text()
            chk["audit_contains_file_content"] = "hello from ULTRON" in raw
            chk["audit_mode"] = oct((base / "state/ultron/audit.jsonl").stat().st_mode & 0o777)
        chk["socket_modes"] = {p.name: oct(p.stat().st_mode & 0o777) for p in run.glob("*.sock")}
        chk["runtime_dir_mode"] = oct(run.stat().st_mode & 0o777)
    finally:
        server.terminate()
        try:
            await asyncio.to_thread(server.wait, 10)
        except subprocess.TimeoutExpired:
            server.send_signal(12)  # SIGUSR2 (debug wrapper dumps asyncio tasks)
            await asyncio.sleep(1)
            server.send_signal(6)  # SIGABRT: faulthandler dumps every thread's stack
            print("AGENT DID NOT STOP ON SIGTERM:\n", (server.stderr.read() or "")[-3000:])
            raise
    chk["sockets_removed_on_exit"] = not list(run.glob("*.sock"))
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
