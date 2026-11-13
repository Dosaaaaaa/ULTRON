"""Reusable widgets: chat transcript, approval dialog, status strip."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


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


class MicBadge(QLabel):
    """The always-visible truth about the microphone. Colour and words come only from the voice
    process's real state; red is used only while speech is actually being transcribed."""

    _TEXT = {
        "off": "MIC OFF",
        "starting": "VOICE STARTING",
        "standby": "MIC OPEN · wake word only",
        "listening": "● LISTENING",
        "thinking": "MIC OPEN · thinking",
        "speaking": "MIC OPEN · speaking",
        "muted": "MUTED · input discarded",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("MIC OFF", parent)
        self.setObjectName("micOff")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(240)

    def set_state(self, state: str) -> None:
        """In standby the microphone stream is open and scanned for the wake word only; in
        'listening' the speech is being transcribed. 'off' and 'muted' capture nothing."""
        self.setText(self._TEXT.get(state, state.upper()))
        self.setObjectName(
            "micLive"
            if state == "listening"
            else "micOpen"
            if state in ("standby", "thinking", "speaking")
            else "micOff"
        )
        self.style().unpolish(self)
        self.style().polish(self)


class Captions(QWidget):
    """The last exchange as text: what you said and what ULTRON answered. Plain text only (never
    interpreted as markup), clipped to a few lines, and cleared when a new chat starts."""

    MAX_CHARS = 280
    WIDTH = 560

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("captions")
        self.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )  # a plain QWidget ignores the QSS fill
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 12)
        lay.setSpacing(4)
        self.you = self._line("capYou")
        self.ultron = self._line("capUltron")
        lay.addWidget(self.you)
        lay.addWidget(self.ultron)
        self.setFixedWidth(
            self.WIDTH
        )  # a known width, so the text wraps (and is measured) predictably
        self.clear()

    def _line(self, name: str) -> QLabel:
        label = QLabel("")
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _fit(self, label: QLabel) -> None:
        """A word-wrapped label inside a squeezed layout reports one line of height; reserve the
        height its text really needs at this width so no line is cut off."""
        width = self.WIDTH - 36  # the box width minus its side margins
        rect = label.fontMetrics().boundingRect(
            0, 0, width, 10000, int(Qt.TextFlag.TextWordWrap), label.text()
        )
        label.setMinimumHeight(rect.height() + 2 if label.text() else 0)
        # An aligned child is given its size HINT (one line for a wrapped label), so the box itself
        # must also say how tall it needs to be.
        lay = self.layout()
        heights = [w.minimumHeight() for w in (self.you, self.ultron) if w.text()]
        m = lay.contentsMargins()
        spacing = lay.spacing() * max(len(heights) - 1, 0)
        self.setMinimumHeight(sum(heights) + spacing + m.top() + m.bottom() if heights else 0)
        self.updateGeometry()  # tell the parent layouts the size changed

    def _clip(self, text: str) -> str:
        text = " ".join(text.split())
        return text if len(text) <= self.MAX_CHARS else text[: self.MAX_CHARS - 1].rstrip() + "…"

    def set_you(self, text: str) -> None:
        self.you.setText("YOU   " + self._clip(text) if text.strip() else "")
        self.ultron.setText("")  # a new question: the old answer is no longer the current one
        self._fit(self.you)
        self._fit(self.ultron)
        self.setVisible(bool(text.strip()))

    def set_ultron(self, text: str) -> None:
        self.ultron.setText("ULTRON   " + self._clip(text) if text.strip() else "")
        self._fit(self.ultron)
        self.setVisible(bool(self.you.text() or self.ultron.text()))

    def clear(self) -> None:
        self.you.setText("")
        self.ultron.setText("")
        self.hide()


class StatusDots(QLabel):
    """A quiet row under the wordmark: which parts of ULTRON are up. Each dot says what it is."""

    NAMES = ("backend", "agent", "phone")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dots")
        self.setTextFormat(Qt.TextFormat.RichText)
        self._state = {name: "off" for name in self.NAMES}
        self._detail = {name: "not checked yet" for name in self.NAMES}
        self._render()

    def set(self, name: str, state: str, detail: str) -> None:
        """state: ok | warn | off."""
        self._state[name], self._detail[name] = state, detail
        self._render()

    def _render(self) -> None:
        colour = {"ok": "#4cc38a", "warn": "#e0b341", "off": "#566170"}
        self.setText(
            "&nbsp;&nbsp;".join(
                f'<span style="color:{colour[self._state[n]]}">&#9679;</span> '
                f'<span style="color:#7fb7e6">{n}</span>'
                for n in self.NAMES
            )
        )
        self.setToolTip("\n".join(f"{n}: {self._detail[n]}" for n in self.NAMES))


class PanelHeader(QWidget):
    """Every side panel opens with the same header: what it is, and a close button. Sentence case,
    one weight; the panel never relies on the toolbar button alone to be closed."""

    closeRequested = Signal()  # noqa: N815

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from ultron_desktop import icons  # noqa: PLC0415 - avoids a cycle at import time

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 4)
        self.title = QLabel(title)
        self.title.setObjectName("panelTitle")
        self.extra = QHBoxLayout()  # room for a panel's own action (refresh)
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("iconBtn")
        self.close_btn.setIcon(icons.icon("close"))
        self.close_btn.setToolTip("Close")
        self.close_btn.setAccessibleName(f"Close {title.lower()}")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.clicked.connect(self.closeRequested.emit)
        row.addWidget(self.title, 1)
        row.addLayout(self.extra)
        row.addWidget(self.close_btn)

    def add_action(self, button: QPushButton) -> None:
        self.extra.addWidget(button)


class ConnectionCard(QWidget):
    """Shown in the middle of the window when the backend cannot be reached: says what is wrong,
    what to do, and offers Retry. Hidden whenever everything is fine."""

    retryRequested = Signal()  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)
        self.title = QLabel("ULTRON cannot reach its backend")
        self.title.setObjectName("cardTitle")
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.cmd = QLabel("")
        self.cmd.setObjectName("data")
        self.cmd.setWordWrap(True)
        self.cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.retry = QPushButton("Try again")
        self.retry.setObjectName("primary")
        self.retry.clicked.connect(self.retryRequested.emit)
        for w in (self.title, self.body, self.cmd):
            lay.addWidget(w)
        lay.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignLeft)
        self.setMaximumWidth(460)
        self.hide()

    def show_problem(self, kind: str) -> None:
        if kind == "unauthorised":
            self.title.setText("ULTRON does not recognise this window")
            self.body.setText(
                "The backend rejected the API token. Check ULTRON_API_TOKEN in backend/.env and "
                "restart both, then try again."
            )
            self.cmd.setText("")
        else:
            self.title.setText("ULTRON cannot reach its backend")
            self.body.setText("It is not running, or it is starting. Start it from a terminal:")
            self.cmd.setText("bin/ultron start")
            self.body.setText(
                "It is not running, or it is still starting. Start it from a terminal, or run "
                "bin/ultron doctor to see what is missing."
            )
        self.show()

    def clear(self) -> None:
        self.hide()
