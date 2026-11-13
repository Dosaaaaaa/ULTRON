"""Agent configuration: approved roots and approved applications.

Stored as JSON in the user's own config directory. Nothing here is system-wide. Roots and
applications start EMPTY except for one workspace folder: the user adds more explicitly
(`ultron-agent roots add ...`), which is the act of approval.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Never acceptable as an approved root (too broad or system-owned).
_FORBIDDEN_ROOTS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run", "/sbin",
    "/sys", "/usr", "/var", "/home", "/opt", "/snap", "/srv", "/tmp",  # noqa: S108
}  # fmt: skip


class ConfigError(ValueError):
    pass


class AppEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    argv: list[str] = Field(min_length=1, max_length=16)


class Root(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    write: bool = False


class StoredConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    roots: list[Root] = []
    apps: dict[str, AppEntry] = {}
    approval_timeout_s: float = Field(default=90.0, ge=5, le=600)
    allow_p4: bool = False


def default_dirs() -> tuple[Path, Path, Path]:
    """(config_dir, state_dir, runtime_dir) following the XDG spec, all under the user's home."""
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "ultron"
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local/state") / "ultron"
    runtime_base = os.environ.get("XDG_RUNTIME_DIR")
    runtime = (
        Path(runtime_base) / "ultron"
        if runtime_base
        else Path(tempfile.gettempdir()) / (f"ultron-{os.getuid()}")
    )
    return config, state, runtime


def private_dir(path: Path) -> Path:
    """Create `path` with mode 0700 (and verify we own it); refuse if it is not private."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.stat()
    if st.st_uid != os.getuid():
        raise ConfigError(f"{path} is not owned by the current user")
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        path.chmod(0o700)
    return path


@dataclass
class AgentConfig:
    config_dir: Path
    state_dir: Path
    runtime_dir: Path
    stored: StoredConfig = field(default_factory=StoredConfig)
    trash_dir: Path = field(
        default_factory=lambda: (
            Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "Trash"
        )
    )

    # ---------------------------------------------------------------- construction
    @classmethod
    def load(
        cls,
        config_dir: Path | None = None,
        state_dir: Path | None = None,
        runtime_dir: Path | None = None,
    ) -> AgentConfig:
        d_conf, d_state, d_run = default_dirs()
        cfg = cls(config_dir or d_conf, state_dir or d_state, runtime_dir or d_run)
        path = cfg.file
        if path.exists():
            try:
                cfg.stored = StoredConfig.model_validate(json.loads(path.read_text()))
            except (json.JSONDecodeError, ValidationError) as e:
                raise ConfigError(f"invalid agent config {path}: {type(e).__name__}") from e
        else:
            ws = Path.home() / "ULTRON_Workspace"
            cfg.stored.roots = [Root(path=str(ws), write=True)]
        return cfg

    @property
    def file(self) -> Path:
        return self.config_dir / "agent.json"

    @property
    def audit_file(self) -> Path:
        return self.state_dir / "audit.jsonl"

    def save(self) -> None:
        private_dir(self.config_dir)
        tmp = self.file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(self.stored.model_dump(), fh, indent=2)
        tmp.replace(self.file)

    # ---------------------------------------------------------------- roots
    def read_roots(self) -> list[Path]:
        return [r for r in self._resolved_roots()]

    def write_roots(self) -> list[Path]:
        return [
            p for p, r in zip(self._resolved_roots(), self.stored.roots, strict=True) if r.write
        ]

    def _resolved_roots(self) -> list[Path]:
        return [Path(r.path).expanduser().resolve() for r in self.stored.roots]

    @staticmethod
    def validate_root(path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise ConfigError("root must be an absolute path")
        p = p.resolve()
        if str(p) in _FORBIDDEN_ROOTS or p == Path.home().resolve() or len(p.parts) < 3:
            raise ConfigError(f"{p} is too broad to approve as a root")
        if not p.is_dir():
            raise ConfigError(f"{p} is not an existing directory")
        return p

    def add_root(self, path: str, write: bool = False) -> Path:
        p = self.validate_root(path)
        self.stored.roots = [r for r in self.stored.roots if Path(r.path).resolve() != p]
        self.stored.roots.append(Root(path=str(p), write=write))
        return p

    def remove_root(self, path: str) -> bool:
        p = Path(path).expanduser().resolve()
        before = len(self.stored.roots)
        self.stored.roots = [r for r in self.stored.roots if Path(r.path).resolve() != p]
        return len(self.stored.roots) != before

    # ---------------------------------------------------------------- apps
    def add_app(self, app_id: str, name: str, argv: list[str]) -> None:
        from ultron_agent.desktop_entries import check_argv

        check_argv(argv)
        if not app_id.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ConfigError("app id may only contain letters, digits, - _ .")
        self.stored.apps[app_id] = AppEntry(name=name, argv=argv)

    def remove_app(self, app_id: str) -> bool:
        return self.stored.apps.pop(app_id, None) is not None

    def summary(self) -> dict[str, Any]:
        return {
            "roots": [{"path": r.path, "write": r.write} for r in self.stored.roots],
            "apps": {k: v.name for k, v in self.stored.apps.items()},
            "approval_timeout_s": self.stored.approval_timeout_s,
        }
