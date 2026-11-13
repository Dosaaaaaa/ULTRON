"""The pairing pop-up: a large QR code to scan with the phone, the code and address for typing it
instead, and a countdown (a code works once and expires in five minutes). It does not block the
window, and closes itself when the code is used up."""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ultron_desktop.phone_panel import qr_pixmap


def seconds_left(expires_at: str, now: datetime | None = None) -> int:
    try:
        end = datetime.fromisoformat(expires_at)
    except ValueError:
        return 0
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0, int((end - (now or datetime.now(UTC))).total_seconds()))


def clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


class PairingDialog(QDialog):
    def __init__(self, link: str | None, code: str, address: str, fingerprint: str | None,
                 expires_at: str, parent=None) -> None:  # fmt: skip
        super().__init__(parent)
        self.setWindowTitle("Pair your phone")
        self.setModal(False)
        self.expires_at = expires_at
        self.code_text = code
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        title = QLabel("Scan this with the ULTRON app")
        title.setObjectName("cardTitle")
        lay.addWidget(title)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if link:
            self.qr.setPixmap(qr_pixmap(link, scale=8, quiet=4))
            lay.addWidget(self.qr)
        else:
            warn = QLabel(
                "There is no QR code yet: the backend is not served over TLS, and the phone app "
                "refuses plain http. Run  bin/ultron phone setup  once, restart ULTRON, then try "
                "again."
            )
            warn.setWordWrap(True)
            warn.setObjectName("note")
            warn.setMaximumWidth(420)
            lay.addWidget(warn)
        self.code = QLabel(code)
        self.code.setObjectName("code")
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.code)
        detail = f"Address  {address}"
        if fingerprint:
            detail += f"\nCertificate  {fingerprint[:23]}…"
        self.info = QLabel(detail)
        self.info.setObjectName("data")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.info)
        self.countdown = QLabel("")
        self.countdown.setObjectName("note")
        lay.addWidget(self.countdown)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy code")
        self.copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.code_text))
        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("primary")
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)
        self._timer.start()
        self.tick()

    def tick(self) -> None:
        left = seconds_left(self.expires_at)
        if left > 0:
            self.countdown.setText(f"Expires in {clock(left)}. It works once.")
            return
        self._timer.stop()
        self.countdown.setText("This code has expired. Close this and create a new one.")
        self.qr.setEnabled(False)  # greyed out: scanning it now would only fail
        self.qr.setGraphicsEffect(None)
        self.copy_btn.setEnabled(False)
        self.code.setEnabled(False)
