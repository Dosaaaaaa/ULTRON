"""Main window: voice only. The blue 3D orb (the web UI in desktop/orb-ui; the native
QPainter orb and head are the fallback), voice controls, a mic status line, and a small
SYS button that reveals the system panel when (and only when) it is clicked.

The device agent's approval dialogs are still wired in: a risky action requested of the agent must
always reach a human, wherever it came from.
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ultron_desktop import icons
from ultron_desktop.agent_link import AgentLink
from ultron_desktop.backend_link import RestClient
from ultron_desktop.hologram import HologramView
from ultron_desktop.memory_panel import MemoryPanel
from ultron_desktop.phone_panel import PhonePanel
from ultron_desktop.settings import Settings, read_wake_name
from ultron_desktop.system_panel import SystemPanel
from ultron_desktop.tray import TrayController
from ultron_desktop.voice_ctl import VoiceController
from ultron_desktop.web_orb import WebOrbView, web_orb_available
from ultron_desktop.widgets import (
    ApprovalDialog,
    Captions,
    ConnectionCard,
    MicBadge,
    StatusDots,
    format_budget,
)

HINTS = {
    "off": "Voice is off.",
    "starting": "Starting the voice pipeline…",
    "standby": "Say “{wake}” to wake me.",
    "listening": "I am listening.",
    "thinking": "Thinking…",
    "speaking": "",
    "muted": "Muted: nothing you say is processed.",
}


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        rest: RestClient | None = None,
        agent: AgentLink | None = None,
        voice: VoiceController | None = None,
        *,
        animate: bool = True,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.rest = rest or RestClient(settings, self)
        self.agent = agent or AgentLink(settings, self)
        self.voice = voice or VoiceController(settings, self)
        self._approvals: list[dict[str, Any]] = []
        self._dialog: ApprovalDialog | None = None
        self._quitting = False
        self.tray = TrayController(self)
        self.setWindowTitle("ULTRON")
        self.resize(980, 860)
        self._build(animate)
        self._wire()
        self._wire_tray()

    # ------------------------------------------------------------ layout
    def _build(self, animate: bool) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        grid = QGridLayout(root)
        grid.setContentsMargins(0, 0, 0, 0)

        # The blue web orb (fork of the MIT ultron-by-sagar-builds UI) when it is built and
        # QtWebEngine is present; otherwise, or with ULTRON_ORB=native, the QPainter orb.
        self._grid = grid
        if animate and os.environ.get("ULTRON_ORB", "web") != "native" and web_orb_available():
            self.hologram = WebOrbView()
            self.hologram.failed.connect(lambda why: self._orb_failed(why))
        else:
            self.hologram = HologramView(animate=animate)
        grid.addWidget(self.hologram, 0, 0)

        # top: the wordmark with its rule on the left, the small SYS button on the right
        top = QWidget()
        top.setObjectName("overlay")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(24, 22, 24, 0)
        title = QVBoxLayout()
        title.setSpacing(6)
        mark = QLabel("U.L.T.R.O.N.")
        mark.setObjectName("wordmark")
        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFixedSize(150, 1)
        title.addWidget(mark, 0, Qt.AlignmentFlag.AlignLeft)
        title.addWidget(rule, 0, Qt.AlignmentFlag.AlignLeft)
        self.dots = StatusDots()
        title.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignLeft)
        self.sys_btn = self._tool_button("System", "system", "System status  (S)")
        self.mem_btn = self._tool_button("Memory", "memory", "What ULTRON remembers  (R)")
        self.phone_btn = self._tool_button("Phone", "phone", "Pair and manage phones  (P)")
        tl.addLayout(title)
        tl.addStretch(1)
        tl.setSpacing(8)
        tl.addWidget(self.phone_btn, 0, Qt.AlignmentFlag.AlignTop)
        tl.addWidget(self.mem_btn, 0, Qt.AlignmentFlag.AlignTop)
        tl.addWidget(self.sys_btn, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(top, 0, 0, Qt.AlignmentFlag.AlignTop)

        # the system panel: hidden until SYS is clicked. It floats over the window (positioned in
        # _place_system, not in the layout) BELOW the SYS button, so nothing covers the button
        # and it can always close the panel again.
        self.system = SystemPanel(self.agent, root)
        self.system.wakeNameChanged.connect(self._on_wake_name)
        self.system.hide()
        self.memory = MemoryPanel(self.rest, root)  # same: hidden until MEM is clicked
        self.memory.hide()
        self.phone = PhonePanel(self.rest, self.settings.backend_url, root)
        self.phone.hide()
        for panel, button in (
            (self.system, self.sys_btn),
            (self.memory, self.mem_btn),
            (self.phone, self.phone_btn),
        ):
            panel.header.closeRequested.connect(lambda b=button: b.setChecked(False))
        self.problem = ConnectionCard(
            root
        )  # in the middle of the orb, only while the backend is unreachable
        self.problem.retryRequested.connect(lambda: self.refresh_dots())
        grid.addWidget(self.problem, 0, 0, Qt.AlignmentFlag.AlignCenter)
        grid.setRowMinimumHeight(0, 520)

        # bottom: key hints left, what it is doing and the mic truth centre, buttons right
        bottom = QWidget()
        bottom.setObjectName("overlay")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(24, 0, 24, 24)
        hints = QVBoxLayout()
        hints.setSpacing(4)
        for key, text in (
            ("V", "voice"),
            ("M", "mute"),
            ("N", "new chat"),
            ("S", "system"),
            ("R", "memory"),
            ("P", "phone"),
        ):
            row = QHBoxLayout()
            row.setSpacing(6)
            k = QLabel(key)
            k.setObjectName("key")
            t = QLabel(text)
            t.setObjectName("keytext")
            row.addWidget(k)
            row.addWidget(t)
            row.addStretch(1)
            hints.addLayout(row)
        centre = QVBoxLayout()
        centre.setSpacing(8)
        self._wake_shown = read_wake_name().capitalize()
        self.hint = QLabel(HINTS["off"])
        self.hint.setObjectName("hint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice = QLabel("")
        self.notice.setObjectName("ghostdim")
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.mic = MicBadge()
        mic_row = QHBoxLayout()
        mic_row.addStretch(1)
        mic_row.addWidget(self.mic)
        mic_row.addStretch(1)
        self.captions = Captions()
        centre.addWidget(self.captions, 0, Qt.AlignmentFlag.AlignHCenter)
        centre.addWidget(self.hint)
        centre.addWidget(self.notice)
        centre.addLayout(mic_row)
        self.voice_btn = QPushButton("Start voice")
        self.voice_btn.setObjectName("primary")  # the one main action while voice is off
        self.voice_btn.setMinimumWidth(170)
        self.mute_btn = QPushButton("Mute")
        self.mute_btn.setObjectName("hud")
        self.mute_btn.setEnabled(False)
        self.new_btn = QPushButton("New chat")
        self.new_btn.setObjectName("hud")
        self.new_btn.setEnabled(False)  # there is no chat to erase until voice is running
        self.new_btn.setToolTip(
            "Erase this conversation for good and start an empty one  (N). "
            "Your saved notes are kept; delete those in MEM."
        )
        buttons = QVBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.voice_btn)
        buttons.addWidget(self.mute_btn)
        buttons.addWidget(self.new_btn)
        bl.addLayout(hints, 1)
        bl.addLayout(centre, 3)
        bl.addLayout(buttons, 1)
        bl.setAlignment(buttons, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        bl.setAlignment(hints, Qt.AlignmentFlag.AlignBottom)
        bl.setAlignment(centre, Qt.AlignmentFlag.AlignBottom)
        grid.addWidget(bottom, 0, 0, Qt.AlignmentFlag.AlignBottom)

    def _wire(self) -> None:
        self.sys_btn.toggled.connect(self._toggle_system)
        self.mem_btn.toggled.connect(self._toggle_memory)
        self.phone_btn.toggled.connect(self._toggle_phone)
        self.new_btn.clicked.connect(self._new_chat)
        # NOTE: lambda, like voice.failed below (bound-method connects to voice signals segfaulted)
        self.voice.conversationReset.connect(lambda ok, msg: self._on_reset(ok, msg))
        # keyboard shortcuts shown as keycaps on screen (V voice, M mute, S system)
        self._keys = [
            QShortcut(QKeySequence("V"), self, activated=self.voice_btn.click),
            QShortcut(QKeySequence("M"), self, activated=self._key_mute),
            QShortcut(QKeySequence("S"), self, activated=self.sys_btn.toggle),
            QShortcut(QKeySequence("R"), self, activated=self.mem_btn.toggle),
            QShortcut(QKeySequence("P"), self, activated=self.phone_btn.toggle),
            QShortcut(QKeySequence("N"), self, activated=self._key_new),
        ]
        self.voice_btn.clicked.connect(self._toggle_voice)
        self.mute_btn.clicked.connect(self.voice.toggle_mute)
        self.voice.stateChanged.connect(self._on_voice_state)
        # NOTE: lambdas, like the other voice signals (bound-method connects to them segfaulted)
        self.voice.heard.connect(lambda text: self.captions.set_you(text))
        self.voice.replied.connect(lambda text: self.captions.set_ultron(text))
        self.voice.levelChanged.connect(lambda v: self._on_level(v))
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
        self.mic.set_state("off")

    def start(self) -> None:
        """Begin background work (kept out of __init__ so tests can build the window quietly)."""
        self.agent.start()
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(15000)
        self._dots_timer.timeout.connect(self.refresh_dots)
        self._dots_timer.start()
        self.refresh_dots()

    def refresh_dots(self) -> None:
        """The three status dots: backend, phone link. (The agent dot follows the agent socket.)"""
        self.rest.get_json("/v1/health/ready", lambda ok, d: self._dots_backend(ok, d))
        self.rest.get_json("/v1/devices", lambda ok, d: self._dots_phone(ok, d))

    def _dots_backend(self, ok: bool, data: Any) -> None:
        if ok:
            self.problem.clear()
        else:
            unauthorised = (data or {}).get("code") == "http_401"
            self.problem.show_problem("unauthorised" if unauthorised else "unreachable")
            self.dots.set(
                "backend",
                "warn" if unauthorised else "off",
                "wrong API token" if unauthorised else "not reachable",
            )
            return
        engines = data.get("engines", [])
        up = [e for e in engines if e.get("reachable")]
        state = "ok" if up and len(up) == len(engines) else "warn"
        self.dots.set("backend", state, f"{len(up)} of {len(engines)} model engines reachable")

    def _dots_phone(self, ok: bool, data: Any) -> None:
        if not ok:
            self.dots.set("phone", "off", "unknown (backend not reachable)")
            return
        live = [d for d in data if not d.get("revoked")]
        online = [d for d in live if d.get("connected")]
        if online:
            self.dots.set("phone", "ok", f"{len(online)} of {len(live)} paired phone(s) connected")
        else:
            self.dots.set("phone", "warn" if live else "off",
                          "paired but not connected" if live else "no phone paired")  # fmt: skip

    # ------------------------------------------------------------ background mode
    def _wire_tray(self) -> None:
        self.tray.showRequested.connect(self.bring_back)
        self.tray.hideRequested.connect(self.hide)
        self.tray.quitRequested.connect(self.quit_app)
        self.tray.voiceToggleRequested.connect(self.voice_btn.click)

    def bring_back(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        """Really quit (the window's close button only hides it while a tray is available)."""
        self._quitting = True
        self.close()
        QApplication.quit()

    def showEvent(self, event) -> None:  # noqa: N802
        self.tray.set_window_visible(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.tray.set_window_visible(False)
        super().hideEvent(event)

    def _key_new(self) -> None:
        if self.new_btn.isEnabled():  # same rule as the button
            self.new_btn.click()

    def _new_chat(self) -> None:
        self.notice.setText("Starting a new chat…")
        self.new_btn.setEnabled(False)  # one at a time; re-enabled when the outcome arrives
        self.voice.new_conversation()

    def _on_reset(self, erased: bool, detail: str) -> None:
        self.captions.clear()  # the old exchange must not stay on screen after NEW CHAT
        if erased:
            self.notice.setText("Chat erased. A new conversation has started.")
        elif "nothing to erase" in detail:
            self.notice.setText("New conversation started (there was nothing to erase).")
        else:  # the working memory was reset, but say plainly that the old chat survived
            self.notice.setText(
                f"New conversation started, but the old chat was NOT erased: {detail}"
            )
        self.new_btn.setEnabled(self.voice.running and self.voice.state not in ("off", "starting"))

    def _key_mute(self) -> None:
        if self.mute_btn.isEnabled():  # same rule as the button: only while voice is running
            self.mute_btn.click()

    # ------------------------------------------------------------ system panel
    def _tool_button(self, label: str, icon_name: str, tip: str) -> QPushButton:
        b = QPushButton(label)
        b.setObjectName("toolbtn")
        b.setCheckable(True)
        b.setIcon(icons.icon(icon_name))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setAccessibleName(label)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    @staticmethod
    def _restyle(widget: QWidget, name: str) -> None:
        """Switch a widget's style role and make Qt re-apply the stylesheet."""
        widget.setObjectName(name)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _place(self, panel: QWidget) -> None:
        root = self.centralWidget()
        panel.adjustSize()
        x = max(root.width() - panel.width() - 24, 0)
        panel.move(x, 58)
        panel.raise_()

    def _place_system(self) -> None:
        self._place(self.system)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        for panel in (self.system, self.memory, self.phone):
            if panel.isVisible():
                self._place(panel)

    def _toggle_memory(self, on: bool) -> None:
        if on:
            self._close_others(self.mem_btn)  # one panel at a time: they all sit in the same place
            self._place(self.memory)
            self.memory.refresh()
        self.memory.setVisible(on)

    def _toggle_phone(self, on: bool) -> None:
        if on:
            self._close_others(self.phone_btn)
            self._place(self.phone)
            self.phone.refresh()
        self.phone.setVisible(on)

    def _close_others(self, keep: QPushButton) -> None:
        for btn in (self.sys_btn, self.mem_btn, self.phone_btn):
            if btn is not keep and btn.isChecked():
                btn.setChecked(False)

    def _toggle_system(self, on: bool) -> None:
        if on:
            self._close_others(self.sys_btn)
            self._place_system()
        self.system.setVisible(on)
        if on:
            self.refresh_health()
            self.system.refresh()
            self._health_timer.start()
        else:
            self._health_timer.stop()  # nothing is polled while the panel is closed

    def refresh_health(self) -> None:
        self.rest.get_json("/v1/health/ready", self._on_health)

    def _on_health(self, ok: bool, data: Any) -> None:
        if not ok:
            code = (data or {}).get("code", "")
            self.system.backend.setText(
                "backend: UNAUTHORISED (check ULTRON_API_TOKEN)"
                if code == "http_401"
                else "backend: offline"
            )
            return
        parts = []
        for e in data.get("engines", []):
            mark = "✓" if e.get("reachable") else ("?" if e.get("reachable") is None else "✗")
            parts.append(f"{e.get('model')} {mark}")
        self.system.backend.setText(f"backend: {data.get('status')} · " + " · ".join(parts))
        if data.get("cloud_configured") and data.get("cloud_allowed", True):
            text, tip = format_budget(data.get("cloud_budget"))
        else:
            text, tip = "cloud: off (local only)", "Cloud is not configured or is disabled."
        self.system.cloud.setText(text)
        self.system.cloud.setToolTip(tip)

    # ------------------------------------------------------------ voice
    def _toggle_voice(self) -> None:
        if self.voice.running:
            self.voice.stop()
        else:
            self.notice.setText("")
            self.voice.start(local_only=self.system.local_only.isChecked())

    def _on_wake_name(self, name: str) -> None:
        self._wake_shown = name.capitalize()  # takes effect in the voice process on its next start

    def _on_voice_failed(self, message: str) -> None:
        self.notice.setText("voice: " + message)  # visible where you are looking

    def _on_level(self, level: float) -> None:
        if isinstance(self.hologram, WebOrbView):  # the native orb has no use for it
            self.hologram.set_level(level)

    def _on_voice_state(self, state: str) -> None:
        self.mic.set_state(state)
        self.hologram.set_state(state)
        self.hint.setText(HINTS.get(state, "").format(wake=self._wake_shown))
        self.voice_btn.setText("Stop voice" if state != "off" else "Start voice")
        self._restyle(self.voice_btn, "hud" if state != "off" else "primary")
        self.mute_btn.setEnabled(state not in ("off", "starting"))
        self.new_btn.setEnabled(state not in ("off", "starting"))
        if state == "off":
            self.captions.clear()
        self.mute_btn.setText("Unmute" if state == "muted" else "Mute")
        self.setWindowTitle("ULTRON — MICROPHONE LIVE" if state == "listening" else "ULTRON")

    # ------------------------------------------------------------ approvals (device agent)
    def _on_agent(self, up: bool) -> None:
        self.dots.set(
            "agent", "ok" if up else "off", "device agent running" if up else "not running"
        )
        if not up:  # a dead agent cannot act on a decision: drop what is waiting
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
            and outcome in ("timeout", "cancelled")
        ):
            self._dialog.resolved_elsewhere()

    def _orb_failed(self, why: str) -> None:
        """Swap the web orb for the native one rather than leave a black screen."""
        old = self.hologram
        self.notice.setText(f"3D orb fell back to the native renderer: {why}")
        self._grid.removeWidget(old)
        old.shutdown()
        old.deleteLater()
        self.hologram = HologramView()
        self._grid.addWidget(self.hologram, 0, 0)
        self.hologram.show()  # added after the window is up, so it is not shown automatically
        self.hologram.lower()  # keep the HUD overlays above it
        self.hologram.set_state(self.voice.state)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tray.active and not self._quitting:
            event.ignore()  # keep ULTRON running; the tray menu has Quit
            self.hide()
            self.tray.tell_once()
            return
        self.tray.shutdown()
        if isinstance(self.hologram, WebOrbView):
            self.hologram.shutdown()
        self.voice.stop()
        self.agent.shutdown()
        self.agent.stop_spawned_agent()
        super().closeEvent(event)
