"""The QR pop-up that appears when a pairing code is created."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import wait_until
from tests.fake_devices_backend import FP, FakeDevicesBackend
from ultron_desktop.main_window import MainWindow
from ultron_desktop.pairing_dialog import PairingDialog, clock, seconds_left


def test_the_countdown_helpers():
    now = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
    assert seconds_left((now + timedelta(minutes=4, seconds=5)).isoformat(), now) == 245
    assert seconds_left((now - timedelta(seconds=1)).isoformat(), now) == 0
    assert seconds_left("not a time", now) == 0
    assert clock(245) == "4:05" and clock(0) == "0:00" and clock(300) == "5:00"


def soon(minutes=5):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def test_the_popup_shows_a_large_qr_the_code_the_address_and_a_countdown(qapp):
    d = PairingDialog(
        "ultron://pair?u=x", "ABCD-EFGH", "https://192.168.0.5:8443", FP, soon(), None
    )
    d.show()
    assert (
        d.qr.pixmap() is not None and d.qr.pixmap().width() > 250
    )  # big enough to scan from a distance
    assert d.code.text() == "ABCD-EFGH" and "192.168.0.5" in d.info.text()
    assert d.countdown.text().startswith("Expires in 4:") or d.countdown.text().startswith(
        "Expires in 5:"
    )
    assert not d.isModal()  # the window stays usable
    d.close()


def test_an_expired_code_greys_out_the_qr_and_says_so(qapp):
    d = PairingDialog("ultron://pair?u=x", "ABCD-EFGH", "https://h:1", FP, soon(-1), None)
    d.show()
    assert "expired" in d.countdown.text() and not d.qr.isEnabled() and not d.copy_btn.isEnabled()
    d.close()


def test_without_tls_there_is_no_qr_and_the_dialog_says_how_to_get_one(qapp):
    d = PairingDialog(None, "ABCD-EFGH", "http://127.0.0.1:8000", None, soon(), None)
    d.show()
    assert d.qr.pixmap() is None or d.qr.pixmap().isNull()
    texts = " ".join(label.text() for label in d.findChildren(type(d.code)))
    assert "bin/ultron phone setup" in texts and "TLS" in texts
    d.close()


def test_the_copy_button_puts_the_code_on_the_clipboard(qapp):
    from PySide6.QtWidgets import QApplication

    d = PairingDialog("ultron://pair?u=x", "WXYZ-1234", "https://h:1", FP, soon(), None)
    d.copy_btn.click()
    assert QApplication.clipboard().text() == "WXYZ-1234"
    d.close()


@pytest.fixture
def devices():
    b = FakeDevicesBackend()
    b.start()
    yield b
    b.stop()


def test_creating_a_code_opens_the_popup_and_a_new_code_replaces_it(qapp, settings, devices):
    settings.backend_url = f"http://127.0.0.1:{devices.port}"
    win = MainWindow(settings, animate=False)
    win.show()
    try:
        win.phone_btn.click()
        wait_until(lambda: win.phone.status.text() != "phones: …", what="loaded")
        win.phone.pair_btn.click()
        wait_until(lambda: getattr(win.phone, "dialog", None) is not None, what="popup")
        first = win.phone.dialog
        assert first.isVisible() and first.qr.pixmap().width() > 250
        win.phone.pair_btn.click()
        wait_until(lambda: win.phone.dialog is not first, what="replaced")
        assert not first.isVisible() and win.phone.dialog.isVisible()
        devices.fingerprint = None  # no certificate: a popup that explains instead of a dead QR
        win.phone.pair_btn.click()
        wait_until(lambda: not win.phone.dialog.qr.isVisible(), what="no-qr popup")
        assert "bin/ultron phone setup" in " ".join(
            label.text() for label in win.phone.dialog.findChildren(type(win.phone.code))
        )
        win.phone.dialog.close()
    finally:
        win.close()
