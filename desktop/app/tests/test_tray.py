"""Background mode: the window hides to a tray instead of quitting, only when a tray exists."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from tests.conftest import wait_until
from ultron_desktop.main_window import MainWindow
from ultron_desktop.tray import TrayController, tray_wanted


def make(settings, qapp, tray_active: bool) -> MainWindow:
    win = MainWindow(settings, animate=False)
    win.tray.active = tray_active
    return win


def test_closing_hides_the_window_and_keeps_everything_running_when_there_is_a_tray(qapp, settings):
    win = make(settings, qapp, tray_active=True)
    win.show()
    wait_until(win.isVisible, what="window shown")
    win.close()
    qapp.processEvents()
    assert not win.isVisible()  # hidden...
    assert win.voice is not None and not win._quitting  # ...not torn down
    assert win.tray.toggle_action.text() == "Show window"
    win.bring_back()
    wait_until(win.isVisible, what="window back")
    assert win.tray.toggle_action.text() == "Hide window"
    win.tray.active = False
    win.close()


def test_without_a_tray_closing_quits_as_before(qapp, settings):
    win = make(settings, qapp, tray_active=False)
    win.show()
    wait_until(win.isVisible, what="window shown")
    win.close()
    qapp.processEvents()
    assert not win.isVisible()
    assert win.tray.icon.isVisible() is False


def test_quit_from_the_tray_really_closes(qapp, settings, monkeypatch):
    win = make(settings, qapp, tray_active=True)
    quit_called = []
    monkeypatch.setattr(QApplication, "quit", lambda: quit_called.append(True))
    win.show()
    wait_until(win.isVisible, what="window shown")
    win.tray.quitRequested.emit()
    qapp.processEvents()
    assert quit_called and win._quitting and not win.isVisible()


def test_tray_menu_actions_drive_the_window(qapp, settings):
    win = make(settings, qapp, tray_active=True)
    win.show()
    wait_until(win.isVisible, what="window shown")
    win.tray.toggle_action.trigger()  # "Hide window"
    qapp.processEvents()
    assert not win.isVisible()
    win.tray.toggle_action.trigger()  # now "Show window"
    wait_until(win.isVisible, what="window back")
    win.tray.active = False
    win.close()


def test_the_tray_can_be_switched_off_and_is_inactive_without_a_system_tray(qapp, monkeypatch):
    monkeypatch.setenv("ULTRON_TRAY", "off")
    assert not tray_wanted()
    assert TrayController().active is False
    monkeypatch.setenv("ULTRON_TRAY", "on")
    assert tray_wanted()
    assert TrayController(available=False).active is False


def test_the_tray_tells_the_user_only_once_that_ultron_is_still_running(qapp, monkeypatch):
    tray = TrayController(available=True)
    shown = []
    monkeypatch.setattr(tray.icon, "showMessage", lambda *a, **k: shown.append(a[0]))
    monkeypatch.setattr(type(tray.icon), "supportsMessages", staticmethod(lambda: True))
    tray.tell_once()
    tray.tell_once()
    assert shown == ["ULTRON is still running"]
