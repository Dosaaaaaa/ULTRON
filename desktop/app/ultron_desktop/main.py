"""Entry point: `ultron-desktop` (or `python -m ultron_desktop`)."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ultron_desktop import fonts, gpu, theme
from ultron_desktop.main_window import MainWindow
from ultron_desktop.settings import Settings


def main() -> int:
    # Must happen before QApplication: the GL/EGL driver is chosen when Qt WebEngine starts.
    print(f"graphics: {gpu.apply()}", flush=True)
    app = QApplication(sys.argv)
    app.setApplicationName("ULTRON")
    app.setDesktopFileName("ultron")  # matches the menu entry, so the window shows its icon
    fonts.load()
    fonts.apply_default(app)
    app.setStyleSheet(theme.QSS)
    win = MainWindow(Settings.load())
    if win.tray.active:  # closing the window only hides it; Quit is in the tray menu
        app.setQuitOnLastWindowClosed(False)
    win.show()
    win.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
