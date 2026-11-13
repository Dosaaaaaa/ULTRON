"""Application launching (P1) from an explicit allowlist. No arguments are accepted from callers."""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 - argv list only, never a shell
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ultron_agent.desktop_entries import check_argv
from ultron_agent.levels import Level
from ultron_agent.registry import ToolContext, ToolSpec
from ultron_agent.tools.system import NoArgs

# The only environment variables a launched application inherits. In particular this keeps API
# keys and tokens that may sit in the agent's own environment away from arbitrary programs.
_ENV_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "DISPLAY", "WAYLAND_DISPLAY",
    "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP", "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "XDG_SESSION_ID", "GDK_BACKEND",
    "QT_QPA_PLATFORM", "PULSE_SERVER", "SHELL", "TERM", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
)  # fmt: skip
_ENV_PREFIX = ("LC_",)


def launch_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOW or k.startswith(_ENV_PREFIX)}


class LaunchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


def app_list(_a: NoArgs, ctx: ToolContext) -> dict[str, Any]:
    return {
        "apps": [
            {"id": k, "name": v.name, "installed": shutil.which(v.argv[0]) is not None}
            for k, v in sorted(ctx.apps.items())
        ]
    }


def app_launch(a: LaunchArgs, ctx: ToolContext) -> dict[str, Any]:
    entry = ctx.apps.get(a.app_id)
    if entry is None:
        raise LookupError("not_allowlisted")
    check_argv(entry.argv)  # defence in depth: a hand-edited config cannot allowlist a shell
    exe = shutil.which(entry.argv[0], path=os.environ.get("PATH"))
    if exe is None:
        raise FileNotFoundError("not_installed")
    proc = subprocess.Popen(  # noqa: S603 - allowlisted argv, no shell, no caller-supplied args
        [exe, *entry.argv[1:]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=launch_env(),
        close_fds=True,
    )
    time.sleep(0.6)  # catch an immediate crash (missing display, bad install)
    code = proc.poll()
    if code not in (None, 0):
        raise RuntimeError(f"exited_immediately:{code}")
    return {"launched": a.app_id, "name": entry.name, "pid": proc.pid, "running": code is None}


SPECS = [
    ToolSpec(
        name="app.list",
        description="List the applications ULTRON is allowed to open.",
        input_model=NoArgs,
        level=Level.P0,
        timeout_s=3,
        handler=app_list,
        describe=lambda a: "app.list",
    ),
    ToolSpec(
        name="app.launch",
        description="Open one allowlisted application by id. No arguments or files can be passed.",
        input_model=LaunchArgs,
        level=Level.P1,
        timeout_s=5,
        handler=app_launch,
        describe=lambda a: f"Open application '{a.app_id}'",
    ),
]
