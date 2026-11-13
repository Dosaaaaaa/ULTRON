"""Client settings. The API token is read from the environment (or, for local development only,
from the git-ignored backend/.env). It is never written to disk by this app, never shown in the UI
and never put on a command line."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _token_from_dotenv(path: Path) -> str | None:
    try:
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "ULTRON_API_TOKEN":
                return value.strip().strip("'\"") or None
    except OSError:
        pass
    return None


def _runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    return Path(base) / "ultron" if base else Path(f"/tmp/ultron-{os.getuid()}")  # noqa: S108


WAKE_NAMES = ("Friday", "Jarvis", "Ultron", "Computer", "Nova", "Orion", "Athena")


def wake_name_file() -> Path:
    """One word, no secrets. The voice process reads the same file."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ultron" / "wake_name"


def read_wake_name() -> str:
    try:
        name = wake_name_file().read_text().strip().lower()
    except OSError:
        name = ""
    return name if name.isalpha() and 3 <= len(name) <= 12 else "friday"


def write_wake_name(name: str) -> None:
    name = name.strip().lower()
    if not (name.isalpha() and 3 <= len(name) <= 12):
        raise ValueError("a wake name is one plain word")
    path = wake_name_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name + "\n")


@dataclass
class Settings:
    backend_url: str = "http://127.0.0.1:8000"
    token: str | None = field(default=None, repr=False)  # repr=False: never printed
    agent_runtime_dir: Path = field(default_factory=_runtime_dir)
    agent_python: Path = REPO / "desktop/agent/.venv/bin/python"
    voice_python: Path = REPO / "desktop/voice/.venv/bin/python"
    # The voice package is not installed into its venv; `python -m ultron_voice` only resolves when
    # started from this directory. (Found the hard way: the Start voice button used the app's cwd.)
    voice_dir: Path = REPO / "desktop/voice"
    voice_state_file: Path = field(
        default_factory=lambda: (
            Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))  # noqa: S108
            / "ultron-voice.state"
        )
    )

    @property
    def voice_level_file(self) -> Path:
        """Loudness of the speech playing now (0..1); the voice process writes it next to the
        state file (see ultron_voice.cli._level_file)."""
        return self.voice_state_file.with_suffix(".level")

    @classmethod
    def load(cls) -> Settings:
        s = cls()
        s.backend_url = os.environ.get("ULTRON_BACKEND_URL", s.backend_url).rstrip("/")
        s.token = os.environ.get("ULTRON_API_TOKEN") or _token_from_dotenv(REPO / "backend/.env")
        if v := os.environ.get("ULTRON_AGENT_RUNTIME_DIR"):
            s.agent_runtime_dir = Path(v)
        return s

    @property
    def ws_url(self) -> str:
        return self.backend_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + (
            "/v1/ws/chat"
        )

    @property
    def console_socket(self) -> Path:
        return self.agent_runtime_dir / "console.sock"
