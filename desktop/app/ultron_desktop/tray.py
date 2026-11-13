"""Run in the background: a tray icon, and closing the window hides it instead of quitting.

The voice process, the device agent and the backend link all belong to the window's process, so
hiding the window keeps ULTRON listening and answering. Quit from the tray menu to stop everything.

Not every desktop has a tray (GNOME needs the AppIndicator extension). When there is none, closing
the window quits as before: hiding a window nobody can bring back would be worse. ULTRON_TRAY=off
turns this off.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def make_icon() -> QIcon:
    """A small blue ring, drawn here so no image file has to ship."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#38bdf8"))
    p.setBrush(QColor(14, 40, 70))
    p.drawEllipse(6, 6, 52, 52)
    p.setBrush(QColor("#38bdf8"))
    p.drawEllipse(24, 24, 16, 16)
    p.end()
    return QIcon(pix)


def tray_wanted() -> bool:
    return os.environ.get("ULTRON_TRAY", "on").lower() not in ("off", "0", "false", "no")


class TrayController(QObject):
    showRequested = Signal()
    hideRequested = Signal()
    quitRequested = Signal()
    voiceToggleRequested = Signal()

    def __init__(self, parent: QObject | None = None, *, available: bool | None = None) -> None:
        super().__init__(parent)
        self.active = (
            tray_wanted() and QSystemTrayIcon.isSystemTrayAvailable()
            if available is None
            else available
        )
        self.icon = QSystemTrayIcon(make_icon(), self)
        self.icon.setToolTip("ULTRON")
        menu = QMenu()
        self.toggle_action = QAction("Hide window", menu)
        self.voice_action = QAction("Start / stop voice", menu)
        self.quit_action = QAction("Quit ULTRON", menu)
        self.toggle_action.triggered.connect(self._toggle)
        self.voice_action.triggered.connect(self.voiceToggleRequested.emit)
        self.quit_action.triggered.connect(self.quitRequested.emit)
        menu.addAction(self.toggle_action)
        menu.addAction(self.voice_action)
        menu.addSeparator()
        menu.addAction(self.quit_action)
        self._menu = menu  # keep a reference: the tray does not own it
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        self._window_visible = True
        self._told = False
        if self.active:
            self.icon.show()

    def set_window_visible(self, visible: bool) -> None:
        self._window_visible = visible
        self.toggle_action.setText("Hide window" if visible else "Show window")

    def _toggle(self) -> None:
        (self.hideRequested if self._window_visible else self.showRequested).emit()

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # a plain click
            self._toggle()

    def tell_once(self) -> None:
        """The first time the window is hidden, say that ULTRON is still running."""
        if self.active and not self._told and QSystemTrayIcon.supportsMessages():
            self._told = True
            self.icon.showMessage(
                "ULTRON is still running",
                "The window is hidden. Click the tray icon to bring it back, or choose Quit.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def shutdown(self) -> None:
        self.icon.hide()
