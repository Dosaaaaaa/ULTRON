"""ultron-agent command line.

ultron-agent serve                 run the agent (foreground)
ultron-agent roots list|add|remove approve / revoke folders
ultron-agent apps list|discover|add|remove
ultron-agent tools | status | call TOOL '{json}'
ultron-agent audit tail|verify
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys
from pathlib import Path

from ultron_agent.broker import Agent
from ultron_agent.client import AgentCallError, AgentClient
from ultron_agent.config import AgentConfig, ConfigError
from ultron_agent.desktop_entries import discover
from ultron_agent.server import AgentServer


async def cmd_serve(cfg: AgentConfig) -> int:
    for r in cfg.stored.roots:  # the default workspace is created on first run, owner-only
        p = Path(r.path).expanduser()  # noqa: ASYNC240
        if not p.exists() and r.write and p.parent.is_dir():
            p.mkdir(mode=0o700)
            print(f"created workspace {p}")
    agent = Agent(cfg)
    server = AgentServer(agent, cfg.runtime_dir)
    await server.start()
    print("ultron-agent ready")
    print(f"  request socket: {server.request_path}")
    print(f"  console socket: {server.console_path}")
    print("roots:", ", ".join(f"{r.path}{' (rw)' if r.write else ''}" for r in cfg.stored.roots))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await server.stop()
    return 0


async def cmd_client(cfg: AgentConfig, args) -> int:
    sock = cfg.runtime_dir / ("console.sock" if args.cmd in ("audit",) else "request.sock")
    try:
        async with AgentClient(sock) as c:
            if args.cmd == "tools":
                out = await c.simple("tools")
                for t in out:
                    print(f"{t['level']}  {t['name']:16} {t['description']}")
                return 0
            if args.cmd == "status":
                print(json.dumps(await c.simple("status"), indent=2))
                return 0
            if args.cmd == "call":
                res = await c.call(args.tool, json.loads(args.args))
                print(json.dumps(res, indent=2, ensure_ascii=False))
                return 0
            if args.audit_cmd == "tail":
                for rec in await c.simple("audit_tail", n=args.n):
                    print(json.dumps(rec, ensure_ascii=False))
                return 0
            v = await c.simple("audit_verify")
            print(
                "audit chain OK" if v["ok"] else f"audit chain BROKEN: {v}",
                f"({v['records']} records)",
            )
            return 0 if v["ok"] else 1
    except (OSError, ConnectionError):
        print("ultron-agent is not running (start it with: ultron-agent serve)", file=sys.stderr)
        return 2
    except AgentCallError as e:
        print(f"{e.code}: {e.message}", file=sys.stderr)
        return 1


def cmd_config(cfg: AgentConfig, args) -> int:
    if args.cmd == "roots":
        if args.sub == "add":
            p = cfg.add_root(args.path, write=args.write)
            cfg.save()
            print(f"approved {p} ({'read/write' if args.write else 'read-only'})")
        elif args.sub == "remove":
            print("removed" if cfg.remove_root(args.path) else "not found")
            cfg.save()
        else:
            for r in cfg.stored.roots:
                print(f"{'rw' if r.write else 'ro'}  {r.path}")
        return 0
    if args.sub == "discover":
        for c in discover():
            print(f"{c.app_id:40} {c.name}")
    elif args.sub == "add":
        cands = {c.app_id: c for c in discover()}
        if args.app_id in cands:
            c = cands[args.app_id]
            cfg.add_app(args.app_id, c.name, c.argv)
        elif args.cmdline:
            import shlex

            cfg.add_app(args.app_id, args.name or args.app_id, shlex.split(args.cmdline))
        else:
            raise ConfigError("unknown app id; use 'apps discover', or pass --cmdline")
        cfg.save()
        print(f"allowlisted {args.app_id}")
    elif args.sub == "remove":
        print("removed" if cfg.remove_app(args.app_id) else "not found")
        cfg.save()
    else:
        for k, v in cfg.stored.apps.items():
            print(f"{k:30} {v.name:24} {' '.join(v.argv)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ultron-agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("tools")
    sub.add_parser("status")
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")
    r = sub.add_parser("roots")
    rs = r.add_subparsers(dest="sub", required=True)
    rs.add_parser("list")
    ra = rs.add_parser("add")
    ra.add_argument("path")
    ra.add_argument("--write", action="store_true", help="allow creating/modifying files here")
    rr = rs.add_parser("remove")
    rr.add_argument("path")
    a = sub.add_parser("apps")
    as_ = a.add_subparsers(dest="sub", required=True)
    as_.add_parser("list")
    as_.add_parser("discover")
    aa = as_.add_parser("add")
    aa.add_argument("app_id")
    aa.add_argument("--name")
    aa.add_argument("--cmdline", help="explicit command line instead of a discovered .desktop file")
    ar = as_.add_parser("remove")
    ar.add_argument("app_id")
    au = sub.add_parser("audit")
    aus = au.add_subparsers(dest="audit_cmd", required=True)
    at = aus.add_parser("tail")
    at.add_argument("-n", type=int, default=20)
    aus.add_parser("verify")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = AgentConfig.load()
        if args.cmd in ("roots", "apps"):
            return cmd_config(cfg, args)
        if args.cmd == "serve":
            with contextlib.suppress(KeyboardInterrupt):
                return asyncio.run(cmd_serve(cfg))
            return 0
        return asyncio.run(cmd_client(cfg, args))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
