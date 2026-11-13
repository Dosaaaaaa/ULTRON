"""Reusable widgets: chat transcript, approval dialog, status strip."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ultron_desktop import theme


class ChatView(QTextBrowser):
    """Plain-text transcript. Text is inserted verbatim (never interpreted as HTML), so a model
    reply or a file name cannot inject markup into the UI."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setReadOnly(True)

    def _write(self, text: str, color: str, bold: bool = False) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt.setFontWeight(700 if bold else 400)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(text, fmt)
        self.setTextCursor(cur)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def add_user(self, text: str) -> None:
        self._write("\nYOU\n", theme.DIM, bold=True)
        self._write(text + "\n", theme.TEXT)

    def begin_assistant(self) -> None:
        self._write("\nULTRON\n", theme.ACCENT, bold=True)

    def add_delta(self, text: str) -> None:
        self._write(text, theme.TEXT)

    def end_assistant(self) -> None:
        self._write("\n", theme.TEXT)

    def add_notice(self, text: str, warn: bool = False) -> None:
        self._write(f"[{text}]\n", theme.WARN if warn else theme.DIM)

    def add_error(self, text: str) -> None:
        self._write(f"[error] {text}\n", theme.DANGER)

    def show_history(self, messages: list[dict[str, Any]]) -> None:
        self.clear()
        for m in messages:
            if m.get("role") == "user":
                self.add_user(m.get("content", ""))
            else:
                self.begin_assistant()
                self.add_delta(m.get("content", ""))
                self.end_assistant()


class ApprovalDialog(QDialog):
    """Shows exactly what will happen. Deny is the default button; Enter never approves.
    For P3+ the Approve button stays disabled until the required phrase is typed exactly."""

    decided = Signal(str, bool, object)  # approval id, approve, confirmation text or None

    def __init__(self, approval: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.approval = approval
        self.setWindowTitle("ULTRON requests permission")
        self.setModal(True)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        level = approval.get("level", "P2")
        needs_text = approval.get("confirmation_required")
        head = QLabel(
            f"{level} · " + ("typed confirmation required" if needs_text else "approval required")
        )
        head.setTextFormat(Qt.TextFormat.PlainText)
        head.setObjectName("dim")
        lay.addWidget(head)
        summary = QLabel(approval.get("summary", ""))
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        summary.setStyleSheet("font-size: 15px; font-weight: 600; padding: 6px 0;")
        lay.addWidget(summary)
        details = QPlainTextEdit(json.dumps(approval.get("args", {}), indent=2, ensure_ascii=False))
        details.setObjectName("mono")
        details.setReadOnly(True)
        details.setMaximumHeight(150)
        lay.addWidget(details)
        src = QLabel(f"Requested via: {approval.get('requested_by', '?')}")
        src.setObjectName("dim")
        src.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(src)

        self.confirm_edit: QLineEdit | None = None
        if needs_text:
            hint = QLabel("To confirm, type exactly:  " + needs_text)
            hint.setTextFormat(Qt.TextFormat.PlainText)
            lay.addWidget(hint)
            self.confirm_edit = QLineEdit()
            self.confirm_edit.setPlaceholderText(needs_text)
            self.confirm_edit.textChanged.connect(self._sync)
            lay.addWidget(self.confirm_edit)

        self.countdown = QLabel("")
        self.countdown.setObjectName("dim")
        lay.addWidget(self.countdown)
        row = QHBoxLayout()
        row.addStretch(1)
        self.deny_btn = QPushButton("Deny")
        self.deny_btn.setDefault(True)
        self.approve_btn = QPushButton("Approve")
        self.approve_btn.setObjectName("primary")
        self.approve_btn.setAutoDefault(False)
        self.deny_btn.clicked.connect(self._deny)
        self.approve_btn.clicked.connect(self._approve)
        row.addWidget(self.deny_btn)
        row.addWidget(self.approve_btn)
        lay.addLayout(row)
        self._left = int(approval.get("expires_in_s", 90))
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)
        self._tick()
        self._sync()
        self._done = False

    def _sync(self) -> None:
        need = self.approval.get("confirmation_required")
        ok = True if not need else (self.confirm_edit.text().strip() == need)  # type: ignore[union-attr]
        self.approve_btn.setEnabled(ok)

    def _tick(self) -> None:
        self.countdown.setText(f"Denied automatically in {self._left}s")
        if self._left <= 0:
            self._deny()
        self._left -= 1

    def _finish(self, approve: bool) -> None:
        if getattr(self, "_done", False):
            return
        self._done = True
        self._tick_timer.stop()
        text = self.confirm_edit.text().strip() if (approve and self.confirm_edit) else None
        self.decided.emit(self.approval["id"], approve, text)
        self.accept() if approve else self.reject()

    def _approve(self) -> None:
        self._finish(True)

    def _deny(self) -> None:
        self._finish(False)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override; closing the window = deny
        self._finish(False)
        super().closeEvent(event)

    def resolved_elsewhere(self) -> None:
        """The agent already settled this request (timeout, requester left)."""
        self._done = True
        self._tick_timer.stop()
        self.reject()


class ElidedLabel(QLabel):
    """A label that shortens itself with an ellipsis when space is tight. `text()` still returns
    the full text and the tooltip shows it."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def text(self) -> str:  # noqa: D102 - full text, not the elided one
        return self._full

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(self.width() - 2, 10)
        )
        super().setText(shown)


def _k(n: int) -> str:
    return f"{n / 1000:.0f}K" if n >= 10_000 else f"{n / 1000:.1f}K" if n >= 1000 else str(n)


def format_budget(b: dict[str, Any] | None) -> tuple[str, str]:
    """(label, tooltip) for the cloud quota. This program's own estimate, corrected whenever the
    provider reports its real numbers; other programs spend the same quota."""
    if not b:
        return "cloud budget: not tracked", "No limits are configured (ULTRON_CLOUD_LIMIT_*)."
    limit = b.get("tokens_limit_24h")
    used = b.get("tokens_used_24h", 0)
    if b.get("cooldown_s", 0) > 0:
        secs = b["cooldown_s"]
        wait = f"{secs}s" if secs < 120 else f"{secs // 60}m"
        text = f"cloud: PAUSED {wait} (provider limit) · using local"
    elif limit:
        text = f"cloud: {round(100 * used / limit)}% of day · {_k(used)}/{_k(limit)} tokens"
    else:
        text = f"cloud: {_k(used)} tokens in 24h"
    tip = (
        f"Tokens, last 24 h (rolling): {used}" + (f" of {limit}" if limit else "") + "\n"
        f"Tokens, last minute: {b.get('tokens_used_1m', 0)}"
        + (f" of {b['tokens_limit_1m']}" if b.get("tokens_limit_1m") else "")
        + f"\nRequests, last 24 h: {b.get('requests_24h', 0)}"
        + (f" of {b['requests_limit_24h']}" if b.get("requests_limit_24h") else "")
        + "\nThis is ULTRON's own count, corrected when the provider reports its numbers. The"
        " quota belongs to the provider account, so other programs spending it are not counted"
        " until the provider says so. Over budget, answers come from the local model."
    )
    return text, tip


class StatusStrip(QWidget):
    """One line of always-visible truth: backend, engine, device agent, microphone."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.backend = ElidedLabel("backend: …")
        self.cloud = ElidedLabel("cloud budget: –")
        self.engine = ElidedLabel("engine: –")
        self.agent = ElidedLabel("device agent: –")
        self.mic = QLabel("MIC OFF")
        self.mic.setObjectName("micOff")
        for w, stretch in ((self.backend, 5), (self.cloud, 3), (self.engine, 3), (self.agent, 2)):
            lay.addWidget(w, stretch)
            lay.addSpacing(10)
        self.mic.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.mic.setMinimumWidth(230)
        self.mic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.mic)

    _MIC_TEXT = {
        "off": "MIC OFF",
        "starting": "VOICE STARTING",
        "standby": "MIC OPEN · wake word only",
        "listening": "● LISTENING",
        "thinking": "MIC OPEN · thinking",
        "speaking": "MIC OPEN · speaking",
        "muted": "MUTED · input discarded",
    }

    def set_mic(self, state: str) -> None:
        """Honest labels: in standby the microphone stream is open and is scanned for the wake word
        only; in 'listening' the speech is being transcribed. 'off' and 'muted' capture nothing."""
        self.mic.setText(self._MIC_TEXT.get(state, state.upper()))
        self.mic.setObjectName(
            "micLive"
            if state == "listening"
            else "micOpen"
            if state in ("standby", "thinking", "speaking")
            else "micOff"
        )
        self.mic.style().unpolish(self.mic)
        self.mic.style().polish(self.mic)
