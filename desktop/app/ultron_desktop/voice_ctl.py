"""Voice controls. The voice pipeline runs as its own process (own venv, own permissions); this
class only starts/stops it, toggles mute (SIGUSR1) and mirrors the status file it writes.

The desktop app never touches the microphone itself. The microphone is open only while the voice
process is running AND its state is "listening"; the UI shows that state permanently.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ultron_desktop.settings import Settings, read_wake_name

STATES = {"standby", "listening", "thinking", "speaking", "muted"}
NEW_MARKER = "[NEW CONVERSATION]"  # printed by ultron_voice.cli.Console; keep in step


def read_state(path: Path) -> str | None:
    try:
        label = path.read_text().strip()
    except OSError:
        return None
    return label if label in STATES else None


def read_level(path: Path) -> float:
    """The speech loudness 0..1 published by the voice process; anything unreadable is silence."""
    try:
        value = float(path.read_text().strip())
    except (OSError, ValueError):
        return 0.0
    return min(1.0, max(0.0, value)) if value == value else 0.0  # NaN -> 0


class VoiceController(QObject):
    heard = Signal(str)  # what you said, as the speech recogniser understood it
    replied = Signal(str)  # what ULTRON answered (the text; the voice may have been cut short)
    stateChanged = Signal(str)  # noqa: N815 - off|starting|standby|listening|thinking|speaking|muted
    levelChanged = Signal(float)  # noqa: N815 - speech loudness 0..1, only emitted while speaking
    conversationReset = Signal(bool, str)  # noqa: N815 - (erased, message) after NEW
    failed = Signal(str)

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.state = "off"
        self._proc: QProcess | None = None
        self._stopping = False
        self._tail: list[str] = []  # last output lines, shown if the process dies on its own
        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._refresh)
        self._level_timer = QTimer(self)  # runs only while speaking
        self._level_timer.setInterval(50)
        self._level_timer.timeout.connect(self._read_level)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            if state == "speaking":
                self._level_timer.start()
            else:
                self._level_timer.stop()
                self.levelChanged.emit(0.0)
            self.stateChanged.emit(state)

    def _read_level(self) -> None:
        self.levelChanged.emit(read_level(self.settings.voice_level_file))

    def start(self, mode: str = "command", local_only: bool = False) -> None:
        if self.running:
            return
        py = self.settings.voice_python
        if not py.exists():
            self.failed.emit("voice environment not found: " + str(py))
            return
        if not self.settings.token:
            self.failed.emit("ULTRON_API_TOKEN is not set; the voice client cannot authenticate")
            return
        self.settings.voice_state_file.unlink(missing_ok=True)
        self.settings.voice_level_file.unlink(missing_ok=True)
        self._stopping = False
        self._tail = []
        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(str(self.settings.voice_dir))
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._read_output)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("ULTRON_API_TOKEN", self.settings.token)  # via environment, not argv
        env.insert("ULTRON_VOICE_BACKEND_URL", self.settings.ws_url)
        env.insert("ULTRON_VOICE_WAKE_NAME", read_wake_name())
        self._proc.setProcessEnvironment(env)
        self._proc.setProgram(str(py))
        args = ["-m", "ultron_voice", "run", "--mode", mode]
        if local_only:
            args += ["--privacy", "local_only"]
        self._proc.setArguments(args)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)
        self._proc.start()
        self._set("starting")
        self._poll.start()

    def stop(self) -> None:
        if self._proc and self.running:
            self._stopping = True
            self._proc.terminate()  # SIGTERM: voice cleans up, removes its state file
            if not self._proc.waitForFinished(8000):
                self._proc.kill()

    def new_conversation(self) -> None:
        """Ask the voice process to erase this chat and start an empty one (SIGUSR2). The outcome
        comes back as a marker line on its output and is reported through conversationReset."""
        if self.running and self._proc and (pid := self._proc.processId()):
            os.kill(pid, signal.SIGUSR2)

    def toggle_mute(self) -> None:
        if self.running and self._proc and (pid := self._proc.processId()):
            os.kill(pid, signal.SIGUSR1)

    def _refresh(self) -> None:
        if not self.running:
            return
        label = read_state(self.settings.voice_state_file)
        self._set(label or "starting")

    def _read_output(self) -> None:
        if not self._proc:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        token = self.settings.token
        for line in text.splitlines():
            if token:
                line = line.replace(token, "***")  # defence in depth: never display the token
            if line.strip():
                self._tail.append(line.strip()[:300])
            marker = line.strip()
            if marker.startswith("you: "):
                self.heard.emit(marker[5:].strip())
            elif marker.startswith("reply: "):
                self.replied.emit(marker[7:].strip())
            if marker.startswith(NEW_MARKER):
                detail = marker[len(NEW_MARKER) :].strip()
                self.conversationReset.emit(detail == "previous chat erased", detail)
        del self._tail[:-12]

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.failed.emit(f"voice process could not be started ({self.settings.voice_python})")
            self._poll.stop()
            self._set("off")

    def _on_finished(self, code: int = 0, _status: object = None) -> None:
        self._read_output()
        if not self._stopping and (code != 0 or self.state == "starting") and self._proc:
            # It ended on its own before or without being asked to: say why, do not just go dark.
            joined = " ".join(self._tail)
            if "ConnectionRefusedError" in joined or "Connect call failed" in joined:
                self.failed.emit(
                    f"the ULTRON backend is not running at {self.settings.ws_url}. Start it first: "
                    "cd backend && .venv/bin/uvicorn app.main:app_factory --factory "
                    "--host 127.0.0.1 --port 8000"
                )
            else:
                tail = " | ".join(self._tail[-5:]) or "no output"
                self.failed.emit(f"voice process exited (code {code}): {tail}")
        self._poll.stop()
        self._set("off")
