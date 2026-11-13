"""System information (P0, read-only). Reads /proc and /sys directly; no shell, no psutil.

Deliberately omits the hostname, usernames and network addresses: nothing here identifies the
person, so it is safe to show to a language model later.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 - fixed argv only, see _gpu()
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ultron_agent.levels import Level
from ultron_agent.registry import ToolContext, ToolSpec


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k] = int(parts[0]) * 1024
    return out


def _cpu_times() -> tuple[int, int]:
    fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    vals = [int(x) for x in fields]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def _cpu_model() -> str:
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.lower().startswith("model name"):
            return line.partition(":")[2].strip()
    return "unknown"


def _os_pretty() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.partition("=")[2].strip().strip('"')
    except OSError:
        pass
    return "unknown"


def _gpu() -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no user input
            [
                exe,
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        name, used, total, util, temp = [x.strip() for x in out.splitlines()[0].split(",")]
        return {
            "name": name,
            "memory_used_mib": int(used),
            "memory_total_mib": int(total),
            "utilization_percent": int(util),
            "temperature_c": int(temp),
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def system_info(_a: NoArgs, _c: ToolContext) -> dict[str, Any]:
    mem = _meminfo()
    uname = os.uname()
    return {
        "os": _os_pretty(),
        "kernel": uname.release,
        "architecture": uname.machine,
        "cpu_model": _cpu_model(),
        "cpu_threads": os.cpu_count(),
        "memory_total_bytes": mem.get("MemTotal"),
        "uptime_seconds": round(float(Path("/proc/uptime").read_text().split()[0])),
        "gpu": _gpu(),
    }


def system_usage(_a: NoArgs, _c: ToolContext) -> dict[str, Any]:
    t1, i1 = _cpu_times()
    time.sleep(0.25)
    t2, i2 = _cpu_times()
    busy = 100.0 * (1 - (i2 - i1) / max(t2 - t1, 1))
    mem = _meminfo()
    total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    load = os.getloadavg()
    swap_total, swap_free = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    return {
        "cpu_percent": round(busy, 1),
        "load_average": [round(x, 2) for x in load],
        "memory_used_bytes": total - avail,
        "memory_total_bytes": total,
        "memory_percent": round(100.0 * (total - avail) / total, 1) if total else None,
        "swap_used_bytes": swap_total - swap_free,
        "gpu": _gpu(),
    }


_REAL_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "f2fs", "zfs"}


def system_storage(_a: NoArgs, _c: ToolContext) -> dict[str, Any]:
    seen: set[str] = set()
    disks = []
    for line in Path("/proc/mounts").read_text().splitlines():
        dev, mount, fstype = line.split()[:3]
        if fstype not in _REAL_FS or dev in seen:
            continue
        seen.add(dev)
        mount = mount.encode().decode("unicode_escape")
        try:
            u = shutil.disk_usage(mount)
        except OSError:
            continue
        disks.append(
            {
                "mount": mount,
                "filesystem": fstype,
                "total_bytes": u.total,
                "used_bytes": u.used,
                "free_bytes": u.free,
                "used_percent": round(100.0 * u.used / u.total, 1) if u.total else None,
            }
        )
    return {"disks": disks}


def _spec(name: str, desc: str, fn, timeout: float) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=desc,
        input_model=NoArgs,
        level=Level.P0,
        timeout_s=timeout,
        handler=fn,
        describe=lambda a, n=name: n,
    )


SPECS = [
    _spec(
        "system.info", "OS, kernel, CPU model, thread count, RAM size, uptime, GPU.", system_info, 6
    ),
    _spec("system.usage", "Current CPU, memory, swap, load and GPU usage.", system_usage, 6),
    _spec("system.storage", "Mounted disks with total, used and free space.", system_storage, 5),
]
