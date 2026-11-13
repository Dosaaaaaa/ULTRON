"""The small system display. Hidden until the SYS button is clicked.

Shows the things you may need to check but should not have to look at: backend and cloud-budget
state, the device agent, and the machine's CPU / memory / disk / GPU as reported by the device
agent's read-only system tools. Also holds the one setting that changes how voice behaves
(local only).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ultron_desktop.agent_link import AgentLink
from ultron_desktop.settings import WAKE_NAMES, read_wake_name, write_wake_name
from ultron_desktop.widgets import ElidedLabel, PanelHeader


def human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return str(n)


def format_system(info: dict, usage: dict, storage: dict) -> str:
    gpu = usage.get("gpu") or info.get("gpu")
    up = int(info.get("uptime_seconds", 0))
    lines = [
        f"OS        {info.get('os')}  (kernel {info.get('kernel')}, {info.get('architecture')})",
        f"CPU       {info.get('cpu_model')}  ·  {info.get('cpu_threads')} threads",
        f"CPU load  {usage.get('cpu_percent')}%   load avg {usage.get('load_average')}",
        f"Memory    {human_bytes(usage.get('memory_used_bytes'))} used of "
        f"{human_bytes(usage.get('memory_total_bytes'))}  ({usage.get('memory_percent')}%)",
        f"Swap      {human_bytes(usage.get('swap_used_bytes'))} used",
        f"Uptime    {up // 3600} h {up % 3600 // 60} min",
    ]
    if gpu:
        lines.append(
            f"GPU       {gpu['name']}  ·  {gpu['memory_used_mib']}/{gpu['memory_total_mib']} MiB"
            f"  ·  {gpu['utilization_percent']}%  ·  {gpu['temperature_c']} °C"
        )
    lines.append("")
    for d in storage.get("disks", []):
        lines.append(
            f"Disk      {d['mount']:<14} {human_bytes(d['used_bytes'])} / "
            f"{human_bytes(d['total_bytes'])}  ({d['used_percent']}%)  {d['filesystem']}"
        )
    return "\n".join(lines)


class SystemPanel(QFrame):
    def __init__(self, agent: AgentLink, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.agent = agent
        self.setObjectName("panel")
        self.setFixedWidth(440)
        lay = QVBoxLayout(self)
        self.header = PanelHeader("System")
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.header.add_action(self.refresh_btn)
        lay.addWidget(self.header)
        self.backend = ElidedLabel("backend: …")
        self.cloud = ElidedLabel("cloud budget: –")
        self.agent_state = ElidedLabel("device agent: –")
        for w in (self.backend, self.cloud, self.agent_state):
            lay.addWidget(w)
        self.start_agent_btn = QPushButton("Start device agent")
        self.start_agent_btn.clicked.connect(self._start_agent)
        lay.addWidget(self.start_agent_btn)
        self.text = QPlainTextEdit()
        self.text.setObjectName("mono")
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(170)
        self.text.setPlaceholderText("System details appear here when the device agent is running.")
        lay.addWidget(self.text, 1)
        self.local_only = QCheckBox("Local only: voice never uses the cloud")
        lay.addWidget(self.local_only)
        self.wake_row = QLabel("Wake name (say it to wake the voice):")
        lay.addWidget(self.wake_row)
        self.wake = QComboBox()
        self.wake.addItems(WAKE_NAMES)
        current = read_wake_name().capitalize()
        self.wake.setCurrentText(current if current in WAKE_NAMES else WAKE_NAMES[0])
        self.wake.currentTextChanged.connect(self._wake_changed)
        lay.addWidget(self.wake)
        self.msg = QLabel("")
        self.msg.setObjectName("note")
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.msg)
        agent.connectedChanged.connect(self._on_connection)
        self._on_connection(agent.is_connected)

    wakeNameChanged = Signal(str)

    def _wake_changed(self, name: str) -> None:
        try:
            write_wake_name(name)
        except (OSError, ValueError) as exc:
            self.msg.setText(f"Could not save the wake name: {exc}")
            return
        self.msg.setText(f"Wake name is now “{name}”. Stop and start voice to use it.")
        self.wakeNameChanged.emit(name)

    def _start_agent(self) -> None:
        if not self.agent.spawn_agent():
            self.msg.setText(
                "Cannot start the agent (already running, or its environment is missing)."
            )

    def _on_connection(self, up: bool) -> None:
        self.agent_state.setText("device agent: connected" if up else "device agent: not running")
        self.start_agent_btn.setVisible(not up)
        if up and self.isVisible():
            self.refresh()
        if not up:
            self.text.clear()

    def refresh(self) -> None:
        """Read the machine's state through the device agent (read-only system tools)."""
        if not self.agent.is_connected:
            return
        got: dict[str, Any] = {}

        def done(name: str):
            def cb(ok: bool, data: Any) -> None:
                if not ok:
                    e = data or {}
                    code, msg = e.get("code", "error"), e.get("message", "")
                    self.msg.setText(f"{code}: {msg}".strip(": "))
                    return
                got[name] = data
                if len(got) == 3:
                    self.text.setPlainText(format_system(got["info"], got["usage"], got["storage"]))
                    self.msg.setText("")

            return cb

        for name, tool in (("info", "system.info"), ("usage", "system.usage"),
                           ("storage", "system.storage")):  # fmt: skip
            self.agent.call(tool, {}, done(name))
