"""Discover installed applications from .desktop files so the user can ALLOWLIST them explicitly.

Discovery only lists candidates; nothing becomes launchable until `ultron-agent apps add`.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

# Programs that turn "launch an app" into "run arbitrary code". Never allowlistable. Names are
# compared without a trailing version (python3.14 -> python, lua5.4 -> lua) and also by what the
# executable really is (a symlink called "calculator" that points at sh is still sh).
FORBIDDEN_EXECUTABLES = frozenset(
    "sh bash dash zsh fish ksh csh tcsh ash mksh rbash rsh sash env sudo su pkexec doas nsenter "
    "chroot busybox xargs login "
    "python perl ruby node nodejs php lua luajit awk gawk mawk tclsh wish expect eval exec "
    "ssh scp nc ncat socat telnet curl wget "
    # wrappers that run another program
    "nohup setsid timeout nice ionice stdbuf time taskset chrt flock unshare setpriv runuser "
    "systemd-run strace ltrace gdb script watch "
    # terminals and multiplexers: they run whatever is passed with -e or typed
    "xterm uxterm rxvt urxvt konsole gnome-terminal xfce4-terminal mate-terminal lxterminal "
    "alacritty kitty foot wezterm tilix terminator st x-terminal-emulator tmux screen".split()
)
_VERSION_SUFFIX = re.compile(r"[-_.]?\d+(?:\.\d+)*$")
_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")


@dataclass(frozen=True)
class Candidate:
    app_id: str
    name: str
    argv: list[str]


def check_argv(argv: list[str]) -> None:
    from ultron_agent.config import ConfigError

    if not argv or not all(isinstance(a, str) and a and "\x00" not in a for a in argv):
        raise ConfigError("invalid command")
    for name in _executable_names(argv[0]):
        if name in FORBIDDEN_EXECUTABLES:
            raise ConfigError(f"'{name}' can run arbitrary code and cannot be allowlisted")


def _executable_names(argv0: str) -> set[str]:
    """Every name `argv0` goes by: as written, without a version suffix, and as it really is."""
    paths = [Path(os.path.normpath(argv0))]
    found = shutil.which(argv0)
    if found:
        paths.append(Path(os.path.realpath(found)))
    elif os.path.isabs(argv0):
        paths.append(Path(os.path.realpath(argv0)))
    names: set[str] = set()
    for p in paths:
        names.add(p.name)
        names.add(_VERSION_SUFFIX.sub("", p.name))
    return names


def _search_dirs() -> list[Path]:
    home = Path.home()
    dirs = [Path("/usr/share/applications"), Path("/usr/local/share/applications")]
    dirs += [Path(p) / "applications" for p in os.environ.get("XDG_DATA_DIRS", "").split(":") if p]
    dirs += [
        home / ".local/share/applications",
        Path("/var/lib/snapd/desktop/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
        home / ".local/share/flatpak/exports/share/applications",
    ]
    seen: list[Path] = []
    for d in dirs:
        if d not in seen and d.is_dir():
            seen.append(d)
    return seen


def parse_desktop_file(path: Path) -> Candidate | None:
    name = exec_line = None
    in_entry = False
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k == "Name" and name is None:
            name = v
        elif k == "Exec" and exec_line is None:
            exec_line = v
        elif k in ("NoDisplay", "Hidden", "Terminal") and v.lower() == "true":
            return None  # hidden entries, and terminal programs (cannot start without a terminal)
        elif k == "Type" and v != "Application":
            return None
    if not name or not exec_line:
        return None
    try:
        argv = shlex.split(_FIELD_CODES.sub("", exec_line))
        check_argv(argv)
    except (ValueError, Exception):  # noqa: BLE001 - unparsable or forbidden entries are skipped
        return None
    return Candidate(path.stem, name, argv)


def discover() -> list[Candidate]:
    found: dict[str, Candidate] = {}
    for d in _search_dirs():
        for f in sorted(d.glob("*.desktop")):
            try:
                c = parse_desktop_file(f)
            except OSError:
                continue
            if c and c.app_id not in found:
                found[c.app_id] = c
    return sorted(found.values(), key=lambda c: c.name.lower())
