"""The phone display: pair a phone, see paired phones, remove one. Hidden until TEL is clicked.

Pairing gives the phone its OWN token with only the powers ticked here (chat and notifications by
default; "call" lets ULTRON ask the phone to show a call confirmation, it never dials by itself).
The code works once and expires in five minutes. Removing a phone cuts it off at once. The panel
only talks to the backend's /v1/devices API.
"""

from __future__ import annotations

import os
import socket
from typing import Any
from urllib.parse import urlencode, urlsplit

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ultron_desktop.backend_link import RestClient
from ultron_desktop.memory_panel import explain
from ultron_desktop.widgets import ElidedLabel, PanelHeader

CAPS = (
    ("chat", "Chat", True, "The phone can talk to ULTRON."),
    ("notify", "Notifications", True, "ULTRON can send it reminders and messages."),
    ("call", "Call on command", False,
     "“Call Asha” asks the phone to show a confirmation. It never dials by itself."),
    ("reminders", "Reminders", True, "The phone can list, add and cancel reminders."),
)  # fmt: skip


def describe(device: dict) -> str:
    state = (
        "REVOKED"
        if device.get("revoked")
        else ("connected" if device.get("connected") else "offline")
    )
    seen = (device.get("last_seen_at") or "never")[:16].replace("T", " ")
    caps = ", ".join(device.get("capabilities") or []) or "none"
    return f"{device.get('name')}  ·  {state}  ·  last seen {seen}  ·  can: {caps}"


def lan_address() -> str | None:
    """This computer's address on the local network (no packet is sent), or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # a reserved address: only used to pick the interface
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def phone_url(backend_url: str) -> str:
    """The address the phone should use: ULTRON_PHONE_URL, else the backend's own address when it
    is already reachable from the network, else this computer's Wi-Fi address on port 8443."""
    forced = os.environ.get("ULTRON_PHONE_URL", "").strip().rstrip("/")
    if forced:
        return forced
    if not is_loopback(backend_url):
        return backend_url
    ip = lan_address()
    return f"https://{ip}:8443" if ip else "https://<this computer's address>:8443"


def pairing_link(url: str, code: str, fingerprint: str | None) -> str:
    """What the QR code holds. The phone app reads it and asks for nothing else."""
    q = {"u": url, "c": code}
    if fingerprint:
        q["f"] = fingerprint
    return "ultron://pair?" + urlencode(q)


def qr_pixmap(text: str, scale: int = 6, quiet: int = 3) -> QPixmap:
    """A black-on-white QR code (scanners want the quiet border and the plain contrast)."""
    import segno  # noqa: PLC0415 - only needed when a code is shown

    matrix = segno.make(text, error="m", micro=False).matrix
    size = (len(matrix) + 2 * quiet) * scale
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("black"))
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                p.drawRect((x + quiet) * scale, (y + quiet) * scale, scale, scale)
    p.end()
    return QPixmap.fromImage(img)


def is_loopback(url: str) -> bool:
    return (urlsplit(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


class PhonePanel(QFrame):
    def __init__(self, rest: RestClient, backend_url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rest = rest
        self.backend_url = backend_url
        self.setObjectName("panel")
        self.setFixedWidth(560)
        self._devices: dict[str, dict] = {}
        self.ask = self._ask  # tests replace this; it must return True only on an explicit yes

        lay = QVBoxLayout(self)
        self.header = PanelHeader("Phone")
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.header.add_action(self.refresh_btn)
        lay.addWidget(self.header)

        self.status = ElidedLabel("phones: …")
        lay.addWidget(self.status)

        self.empty = QLabel(
            "No phone is paired yet. Choose what it may do below, create a pairing code, and scan "
            "the code with the ULTRON app on the phone."
        )
        self.empty.setObjectName("note")
        self.empty.setWordWrap(True)
        self.empty.hide()
        lay.addWidget(self.empty)
        self.list = QListWidget()
        self.list.setMinimumHeight(90)
        self.list.setMaximumHeight(150)
        self.list.setWordWrap(True)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        lay.addWidget(self.list, 1)
        self.revoke_btn = QPushButton("Remove selected phone")
        self.revoke_btn.clicked.connect(self._revoke)
        lay.addWidget(self.revoke_btn)

        pair = QLabel("Pair a new phone")
        pair.setObjectName("section")
        lay.addWidget(pair)
        self.caps: dict[str, QCheckBox] = {}
        for key, label, default, tip in CAPS:
            box = QCheckBox(label)
            box.setChecked(default)
            box.setToolTip(tip)
            self.caps[key] = box
            lay.addWidget(box)
        self.pair_btn = QPushButton("Create pairing code")
        self.pair_btn.clicked.connect(self._pair)
        lay.addWidget(self.pair_btn)

        self.code = QLabel("")
        self.code.setObjectName("code")
        self.code.setTextFormat(Qt.TextFormat.PlainText)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.how = QLabel("")
        self.how.setWordWrap(True)
        self.how.setTextFormat(Qt.TextFormat.PlainText)
        self.how.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.how.setObjectName("note")
        self.qr = QLabel("")
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setToolTip("Scan this with the ULTRON app on the phone")
        self.qr.hide()
        self.link = ""
        result = QHBoxLayout()  # the instructions on the left, the QR code on the right
        text_col = QVBoxLayout()
        text_col.addWidget(self.code)
        text_col.addWidget(self.how)
        text_col.addStretch(1)
        result.addLayout(text_col, 1)
        result.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(result)

        self.msg = QLabel("")
        self.msg.setObjectName("note")
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.msg)
        self._sync_buttons()

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        self.rest.get_json("/v1/devices", lambda ok, d: self._on_list(ok, d))

    def _on_list(self, ok: bool, data: Any) -> None:
        if not ok:
            self.status.setText("phones: " + explain(data))
            return
        self._devices = {d["id"]: d for d in data}
        self.list.clear()
        for d in data:
            item = QListWidgetItem(describe(d))
            item.setData(Qt.ItemDataRole.UserRole, d["id"])
            self.list.addItem(item)
        self.empty.setVisible(not data)
        self.list.setVisible(bool(data))
        live = [d for d in data if not d.get("revoked")]
        online = sum(1 for d in live if d.get("connected"))
        self.status.setText(f"{len(live)} paired · {online} connected now")
        self._sync_buttons()

    def selected(self) -> dict | None:
        items = self.list.selectedItems()
        return self._devices.get(items[0].data(Qt.ItemDataRole.UserRole)) if items else None

    def _sync_buttons(self) -> None:
        sel = self.selected()
        self.revoke_btn.setEnabled(bool(sel and not sel.get("revoked")))

    # ------------------------------------------------------------ actions
    def _ask(self, text: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)  # Enter never removes by accident
        return box.exec() == QMessageBox.StandardButton.Yes

    def _revoke(self) -> None:
        sel = self.selected()
        if not sel or sel.get("revoked"):
            return
        if not self.ask(
            f"Remove “{sel.get('name')}”? It is cut off at once and must be paired again."
        ):
            return

        def done(ok: bool, data: Any) -> None:
            self.msg.setText("Removed." if ok else explain(data))
            self.refresh()

        self.rest.delete(f"/v1/devices/{sel['id']}", done)

    def _pair(self) -> None:
        wanted = [k for k, box in self.caps.items() if box.isChecked()]
        if not wanted:
            self.msg.setText("Tick at least one thing the phone may do.")
            return
        self.pair_btn.setEnabled(False)

        def done(ok: bool, data: Any) -> None:
            self.pair_btn.setEnabled(True)
            if not ok:
                self.code.setText("")
                self.how.setText("")
                self.msg.setText(explain(data))
                return
            self.msg.setText("")
            self.code.setText(data["code"])
            self.how.setText(self.instructions(data))
            self.show_qr(data)
            self.popup(data)

        self.rest.send_json("POST", "/v1/devices/pairing-code", {"capabilities": wanted}, done)

    def popup(self, data: dict) -> None:
        """A big, separate QR window for the code just made (replacing any earlier one)."""
        from ultron_desktop.pairing_dialog import PairingDialog  # noqa: PLC0415 - avoids an import cycle

        if getattr(self, "dialog", None) is not None:
            self.dialog.close()
        fp = data.get("tls_fingerprint_sha256")
        self.dialog = PairingDialog(
            self.link or None,
            data["code"],
            phone_url(self.backend_url),
            fp,
            data.get("expires_at", ""),
            self,
        )
        self.dialog.setWindowFlag(Qt.WindowType.Window, True)
        self.dialog.show()
        self.dialog.raise_()

    def show_qr(self, data: dict) -> None:
        """A QR code only when the backend has a certificate: the phone app refuses plain http."""
        fp = data.get("tls_fingerprint_sha256")
        if not fp:
            self.link = ""
            self.qr.hide()
            return
        self.link = pairing_link(phone_url(self.backend_url), data["code"], fp)
        self.qr.setPixmap(qr_pixmap(self.link, scale=5))
        self.qr.show()

    def instructions(self, data: dict) -> str:
        fp = data.get("tls_fingerprint_sha256")
        lines = [
            "In the ULTRON app on the phone, enter this code within 5 minutes (it works once).",
        ]
        if fp:
            lines.append(
                "Scan the code above with the ULTRON app (Pair > Scan). To type it in instead, "
                f"enter the code, the address and this fingerprint:\n{fp}"
            )
        else:
            lines.append(
                "WARNING: this backend is not served over TLS (no certificate is configured). The "
                "phone app refuses plain http, so it cannot pair until you set one up "
                "(desktop/packaging/make_lan_cert.sh)."
            )
        addr = phone_url(self.backend_url)
        if is_loopback(self.backend_url):
            lines.append(
                f"Server address for the phone: {addr}\nThe backend is listening on this computer "
                "only. Start it on your Wi-Fi address with the certificate (desktop/packaging/"
                "make_lan_cert.sh prints the command) and allow port 8443 in the firewall."
            )
        else:
            lines.append(f"Server address: {addr}")
        return "\n\n".join(lines)
