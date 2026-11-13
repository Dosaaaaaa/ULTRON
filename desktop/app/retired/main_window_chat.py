"""Main window: chat, status, device panel, voice controls, approval queue."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ultron_desktop.agent_link import AgentLink
from ultron_desktop.backend_link import ChatClient, RestClient
from ultron_desktop.device_panel import DevicePanel
from ultron_desktop.settings import Settings
from ultron_desktop.voice_ctl import VoiceController
from ultron_desktop.widgets import ApprovalDialog, ChatView, StatusStrip, format_budget


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        rest: RestClient | None = None,
        chat: ChatClient | None = None,
        agent: AgentLink | None = None,
        voice: VoiceController | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.rest = rest or RestClient(settings, self)
        self.chat = chat or ChatClient(settings, self)
        self.agent = agent or AgentLink(settings, self)
        self.voice = voice or VoiceController(settings, self)
        self.conversation_id: str | None = None
        self._approvals: list[dict[str, Any]] = []
        self._dialog: ApprovalDialog | None = None
        self.setWindowTitle("ULTRON")
        self.resize(1320, 780)
        self._build()
        self._wire()

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        self.status = StatusStrip()
        outer.addWidget(self.status)
        split = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(split, 1)

        # left: conversations
        left = QWidget()
        lv = QVBoxLayout(left)
        self.new_btn = QPushButton("New conversation")
        self.new_btn.clicked.connect(self.new_conversation)
        self.conv_list = QListWidget()
        self.conv_list.itemClicked.connect(self._open_conversation)
        self.memory_btn = QPushButton("Memory (not available until Phase 4)")
        self.memory_btn.setEnabled(False)
        lv.addWidget(self.new_btn)
        lv.addWidget(self.conv_list, 1)
        lv.addWidget(self.memory_btn)

        # centre: chat + voice
        centre = QWidget()
        cv = QVBoxLayout(centre)
        self.chat_view = ChatView()
        cv.addWidget(self.chat_view, 1)
        self.notice = QLabel("")
        self.notice.setObjectName("dim")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        cv.addWidget(self.notice)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message ULTRON…")
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        row.addWidget(self.stop_btn)
        cv.addLayout(row)
        row2 = QHBoxLayout()
        self.local_only = QCheckBox("Local only (never use the cloud for this conversation)")
        row2.addWidget(self.local_only, 1)
        self.voice_btn = QPushButton("Start voice")
        self.mute_btn = QPushButton("Mute")
        self.mute_btn.setEnabled(False)
        row2.addWidget(self.voice_btn)
        row2.addWidget(self.mute_btn)
        cv.addLayout(row2)

        # right: device
        self.device = DevicePanel(self.agent)
        split.addWidget(left)
        split.addWidget(centre)
        split.addWidget(self.device)
        split.setSizes([240, 640, 440])

    def _wire(self) -> None:
        self.send_btn.clicked.connect(self.send)
        self.input.returnPressed.connect(self.send)
        self.stop_btn.clicked.connect(self.chat.cancel)
        self.chat.started.connect(self._on_started)
        self.chat.delta.connect(self.chat_view.add_delta)
        self.chat.finished.connect(self._on_finished)
        self.chat.failed.connect(self._on_failed)
        self.voice_btn.clicked.connect(self._toggle_voice)
        self.mute_btn.clicked.connect(self.voice.toggle_mute)
        self.voice.stateChanged.connect(self._on_voice_state)
        # NOTE: a lambda, not the bound method. With `connect(self._on_voice_failed)` the test suite
        # segfaulted in ~QObject (QtNetwork, deferred delete after a previous window) on every run;
        # with the lambda it never did. Root cause NOT found (see docs/PHASE_3_DESKTOP.md).
        self.voice.failed.connect(lambda m: self._on_voice_failed(m))
        self.agent.connectedChanged.connect(self._on_agent)
        self.agent.approvalRequested.connect(self._enqueue_approval)
        self.agent.approvalResolved.connect(self._approval_resolved)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(15000)
        self._health_timer.timeout.connect(self.refresh_health)
        self.status.set_mic("off")

    def start(self) -> None:
        """Begin background work (kept out of __init__ so tests can build the window quietly)."""
        self.agent.start()
        self.refresh_health()
        self.refresh_conversations()
        self._health_timer.start()

    # ------------------------------------------------------------ backend status
    def refresh_health(self) -> None:
        self.rest.get_json("/v1/health/ready", self._on_health)

    def _on_health(self, ok: bool, data: Any) -> None:
        if not ok:
            code = (data or {}).get("code", "")
            self.status.backend.setText(
                "backend: UNAUTHORISED (check ULTRON_API_TOKEN)"
                if code == "http_401"
                else "backend: offline"
            )
            return
        parts = []
        for e in data.get("engines", []):
            mark = "✓" if e.get("reachable") else ("?" if e.get("reachable") is None else "✗")
            parts.append(f"{e.get('model')} {mark}")
        self.status.backend.setText(f"backend: {data.get('status')} · " + " · ".join(parts))
        if data.get("cloud_configured") and data.get("cloud_allowed", True):
            text, tip = format_budget(data.get("cloud_budget"))
        else:
            text, tip = "cloud: off (local only)", "Cloud is not configured or is disabled."
        self.status.cloud.setText(text)
        self.status.cloud.setToolTip(tip)

    def refresh_conversations(self) -> None:
        self.rest.get_json("/v1/conversations?limit=50", self._on_conversations)

    def _on_conversations(self, ok: bool, data: Any) -> None:
        if not ok:
            return
        self.conv_list.clear()
        for c in data:
            title = c.get("title") or "Untitled"
            item = QListWidgetItem(
                f"{'🔒 ' if c.get('local_only') else ''}{title}  ({c.get('message_count', 0)})"
            )
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, bool(c.get("local_only")))
            self.conv_list.addItem(item)

    def _open_conversation(self, item: QListWidgetItem) -> None:
        cid = item.data(Qt.ItemDataRole.UserRole)
        locked = bool(item.data(Qt.ItemDataRole.UserRole + 1))

        def cb(ok: bool, data: Any) -> None:
            if not ok:
                self.notice.setText("could not open conversation")
                return
            self.conversation_id = cid
            self.chat_view.show_history(data["messages"])
            self._set_privacy_lock(locked)

        self.rest.get_json(f"/v1/conversations/{cid}", cb)

    def new_conversation(self) -> None:
        self.conversation_id = None
        self.chat_view.clear()
        self.notice.setText("")
        self._set_privacy_lock(False)

    def _set_privacy_lock(self, locked: bool) -> None:
        if locked:
            self.local_only.setChecked(True)
        self.local_only.setEnabled(not locked)
        self.local_only.setText(
            "Local only (locked: this conversation is permanently local)"
            if locked
            else "Local only (never use the cloud for this conversation)"
        )

    # ------------------------------------------------------------ chat
    def send(self) -> None:
        text = self.input.text().strip()
        if not text or self.chat.busy:
            return
        self.input.clear()
        self.chat_view.add_user(text)
        self.notice.setText("")
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._began = False
        self.chat.send(text, self.conversation_id, self.local_only.isChecked())

    def _on_started(self, msg: dict[str, Any]) -> None:
        eng = msg.get("engine", {})
        self.status.engine.setText(f"engine: {eng.get('provider')}/{eng.get('model')}")
        if msg.get("notice"):
            self.notice.setText(msg["notice"])
        self.chat_view.begin_assistant()
        self._began = True

    def _on_finished(self, msg: dict[str, Any]) -> None:
        self.chat_view.end_assistant()
        self.conversation_id = msg.get("conversation_id", self.conversation_id)
        if msg.get("notice"):
            self.notice.setText(msg["notice"])
        if msg.get("interrupted"):
            self.chat_view.add_notice("interrupted")
        if msg.get("privacy_locked"):
            self._set_privacy_lock(True)
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.refresh_conversations()
        self.refresh_health()  # the cloud budget just changed

    def _on_failed(self, code: str, message: str) -> None:
        self.chat_view.add_error(f"{code}: {message}" if message else code)
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------ voice
    def _toggle_voice(self) -> None:
        if self.voice.running:
            self.voice.stop()
        else:
            self.voice.start(local_only=self.local_only.isChecked())

    def _on_voice_failed(self, message: str) -> None:
        self.notice.setText("voice: " + message)
        self.chat_view.add_error("voice: " + message)  # visible where you are looking

    def _on_voice_state(self, state: str) -> None:
        self.status.set_mic(state)
        self.voice_btn.setText("Stop voice" if state != "off" else "Start voice")
        self.mute_btn.setEnabled(state not in ("off", "starting"))
        self.mute_btn.setText("Unmute" if state == "muted" else "Mute")
        self.setWindowTitle("ULTRON — MICROPHONE LIVE" if state == "listening" else "ULTRON")

    # ------------------------------------------------------------ device agent + approvals
    def _on_agent(self, up: bool) -> None:
        self.status.agent.setText("device agent: connected" if up else "device agent: not running")
        if not up:
            self._approvals.clear()
            if self._dialog:
                self._dialog.resolved_elsewhere()
                self._dialog = None

    def _enqueue_approval(self, approval: dict[str, Any]) -> None:
        self._approvals.append(approval)
        self._show_next_approval()

    def _show_next_approval(self) -> None:
        if self._dialog is not None or not self._approvals:
            return
        ap = self._approvals.pop(0)
        dlg = ApprovalDialog(ap, self)
        dlg.decided.connect(self._send_decision)
        dlg.finished.connect(lambda _r, d=dlg: self._dialog_closed(d))
        self._dialog = dlg
        dlg.open()
        dlg.raise_()
        dlg.activateWindow()

    def _send_decision(self, approval_id: str, approve: bool, confirmation: Any) -> None:
        def cb(ok: bool, data: Any) -> None:
            if not ok:
                self.notice.setText(f"decision rejected by the agent: {(data or {}).get('code')}")

        self.agent.request(
            "decide", cb, approval_id=approval_id, approve=approve, confirmation=confirmation
        )

    def _dialog_closed(self, dlg: ApprovalDialog) -> None:
        if self._dialog is dlg:
            self._dialog = None
        dlg.deleteLater()
        QTimer.singleShot(0, self._show_next_approval)

    def _approval_resolved(self, approval_id: str, outcome: str) -> None:
        self._approvals = [a for a in self._approvals if a["id"] != approval_id]
        if (
            self._dialog
            and self._dialog.approval["id"] == approval_id
            and outcome
            in (
                "timeout",
                "cancelled",
            )
        ):
            self._dialog.resolved_elsewhere()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.voice.stop()
        self.chat.close()
        self.agent.shutdown()
        self.agent.stop_spawned_agent()
        super().closeEvent(event)
