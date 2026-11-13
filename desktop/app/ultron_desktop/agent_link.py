"""Console connection to the device agent (QLocalSocket, newline-delimited JSON).

This app connects to console.sock: it is the only client allowed to approve actions.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from ultron_desktop.settings import Settings

Callback = Callable[[bool, Any], None]


class AgentLink(QObject):
    connectedChanged = Signal(bool)  # noqa: N815 - Qt naming
    approvalRequested = Signal(dict)  # noqa: N815
    approvalResolved = Signal(str, str)  # noqa: N815
    auditRecord = Signal(dict)  # noqa: N815

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._sock = QLocalSocket(self)
        self._buf = b""
        self._next = 1
        self._callbacks: dict[int, Callback] = {}
        self._proc: QProcess | None = None
        self._sock.connected.connect(lambda: self.connectedChanged.emit(True))
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.readyRead.connect(self._on_ready)
        self._retry = QTimer(self)
        self._retry.setInterval(1500)
        self._retry.timeout.connect(self.connect_now)

    # ------------------------------------------------------------ connection
    @property
    def is_connected(self) -> bool:
        return self._sock.state() == QLocalSocket.LocalSocketState.ConnectedState

    def start(self) -> None:
        self.connect_now()
        self._retry.start()

    def connect_now(self) -> None:
        if self._sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._sock.connectToServer(str(self.settings.console_socket))

    def shutdown(self) -> None:
        """Stop reconnecting and drop the console connection (the agent keeps running)."""
        self._retry.stop()
        self._sock.abort()

    def spawn_agent(self) -> bool:
        """Start the device agent as a child process (runs under this user, no privileges)."""
        py = self.settings.agent_python
        if self._proc or not py.exists():
            return False
        self._proc = QProcess(self)
        self._proc.setProgram(str(py))
        self._proc.setArguments(["-m", "ultron_agent", "serve"])
        self._proc.start()
        return True

    def stop_spawned_agent(self) -> None:
        if self._proc:
            self._proc.terminate()
            self._proc.waitForFinished(3000)
            self._proc = None

    def _on_disconnected(self) -> None:
        for cb in self._callbacks.values():
            cb(False, {"code": "disconnected", "message": "device agent disconnected"})
        self._callbacks.clear()
        self.connectedChanged.emit(False)

    # ------------------------------------------------------------ protocol
    def request(self, op: str, callback: Callback | None = None, **fields: Any) -> None:
        if not self.is_connected:
            if callback:
                callback(False, {"code": "disconnected", "message": "device agent not running"})
            return
        rid = self._next
        self._next += 1
        if callback:
            self._callbacks[rid] = callback
        self._sock.write((json.dumps({"id": rid, "op": op, **fields}) + "\n").encode())

    def call(self, tool: str, args: dict[str, Any], callback: Callback | None = None) -> None:
        self.request("call", callback, tool=tool, args=args)

    def decide(self, approval_id: str, approve: bool, confirmation: str | None = None) -> None:
        self.request(
            "decide",
            None,
            approval_id=approval_id,
            approve=approve,
            confirmation=confirmation,
        )

    def _on_ready(self) -> None:
        self._buf += bytes(self._sock.readAll().data())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "approval_requested":
            self.approvalRequested.emit(msg["approval"])
        elif event == "approval_resolved":
            self.approvalResolved.emit(msg.get("id", ""), msg.get("outcome", ""))
        elif event == "audit":
            self.auditRecord.emit(msg["record"])
        elif (cb := self._callbacks.pop(msg.get("id"), None)) is not None:
            if msg.get("ok"):
                cb(True, msg.get("result"))
            else:
                cb(False, msg.get("error", {}))


if __name__ == "__main__":  # pragma: no cover
    sys.exit("library module")
