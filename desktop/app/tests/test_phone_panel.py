"""The TEL panel: pairing codes, the phone list, and removing a phone."""

from __future__ import annotations

import pytest

from tests.conftest import TOKEN, wait_until
from tests.fake_devices_backend import FP, FakeDevicesBackend
from ultron_desktop.main_window import MainWindow
from ultron_desktop.phone_panel import describe, is_loopback


@pytest.fixture
def backend():
    b = FakeDevicesBackend()
    b.start()
    yield b
    b.stop()


@pytest.fixture
def win(qapp, settings, backend):
    settings.backend_url = f"http://127.0.0.1:{backend.port}"
    w = MainWindow(settings, animate=False)
    w.show()
    w.phone.ask = lambda text: True
    yield w
    w.close()


def open_panel(win):
    win.phone_btn.click()
    wait_until(lambda: win.phone.status.text() != "phones: …", what="loaded")


def calls(backend, method, path_prefix):
    return [
        r for r in backend.requests if r["method"] == method and r["path"].startswith(path_prefix)
    ]


def test_the_panel_is_hidden_until_tel_is_clicked_and_one_panel_shows_at_a_time(win):
    assert not win.phone.isVisible()
    win.phone_btn.click()
    assert win.phone.isVisible()
    win.mem_btn.click()
    assert win.memory.isVisible() and not win.phone.isVisible() and not win.phone_btn.isChecked()
    win.phone_btn.click()
    assert win.phone.isVisible() and not win.memory.isVisible() and not win.mem_btn.isChecked()
    win.sys_btn.click()
    assert win.system.isVisible() and not win.phone.isVisible()


def test_the_open_panel_never_covers_the_buttons_that_close_it(win):
    win.phone_btn.click()
    root = win.centralWidget()
    top = win.phone.mapTo(root, win.phone.rect().topLeft()).y()
    for btn in (win.phone_btn, win.mem_btn, win.sys_btn):
        assert top >= btn.mapTo(root, btn.rect().bottomLeft()).y(), btn.text()


def test_p_opens_the_panel_and_its_keycap_is_on_screen(win):
    from PySide6.QtWidgets import QLabel

    assert "P" in {w.text() for w in win.findChildren(QLabel) if w.objectName() == "key"}
    next(sc for sc in win._keys if sc.key().toString() == "P").activated.emit()
    assert win.phone.isVisible()


def test_the_list_shows_state_last_seen_and_powers_and_never_a_token(win, backend):
    open_panel(win)
    rows = [win.phone.list.item(i).text() for i in range(win.phone.list.count())]
    assert "Pixel 8" in rows[0] and "connected" in rows[0] and "can: chat, notify" in rows[0]
    assert "REVOKED" in rows[1] and "last seen never" in rows[1]
    assert win.phone.status.text() == "1 paired · 1 connected now"
    assert backend.requests[0]["auth"] == f"Bearer {TOKEN}"
    assert "token" not in " ".join(rows).lower()


def test_creating_a_code_asks_for_only_the_ticked_powers_and_shows_code_and_fingerprint(
    win, backend
):
    open_panel(win)
    win.phone.caps["call"].setChecked(True)
    win.phone.pair_btn.click()
    wait_until(lambda: win.phone.code.text() != "", what="code shown")
    sent = calls(backend, "POST", "/v1/devices/pairing-code")[0]["body"]
    assert sent == {"capabilities": ["chat", "notify", "call", "reminders"]}
    assert win.phone.code.text() == "ABCD-EFGH"
    assert FP in win.phone.how.text() and "5 minutes" in win.phone.how.text()
    assert "listening on this computer only" in win.phone.how.text()  # loopback: say so
    assert "Server address for the phone: https://" in win.phone.how.text()


def test_without_tls_the_panel_warns_that_the_phone_cannot_pair(win, backend):
    backend.fingerprint = None
    open_panel(win)
    win.phone.pair_btn.click()
    wait_until(lambda: win.phone.code.text() != "", what="code shown")
    assert (
        "not served over TLS" in win.phone.how.text() and "make_lan_cert.sh" in win.phone.how.text()
    )


def test_unticking_everything_sends_nothing(win, backend):
    open_panel(win)
    for box in win.phone.caps.values():
        box.setChecked(False)
    win.phone.pair_btn.click()
    assert "Tick at least one" in win.phone.msg.text()
    assert calls(backend, "POST", "/v1/devices/pairing-code") == []


def test_removing_a_phone_asks_first_defaults_to_no_and_then_revokes(win, backend):
    open_panel(win)
    win.phone.list.setCurrentRow(0)
    asked = []
    win.phone.ask = lambda text: asked.append(text) or False
    win.phone.revoke_btn.click()
    assert asked and "Pixel 8" in asked[0] and calls(backend, "DELETE", "/v1/devices/") == []
    win.phone.ask = lambda text: True
    win.phone.revoke_btn.click()
    wait_until(lambda: "REVOKED" in win.phone.list.item(0).text(), what="list refreshed")
    assert calls(backend, "DELETE", "/v1/devices/d1")
    assert win.phone.status.text() == "0 paired · 0 connected now"


def test_a_revoked_phone_cannot_be_removed_again(win):
    open_panel(win)
    win.phone.list.setCurrentRow(1)
    assert not win.phone.revoke_btn.isEnabled()


def test_a_wrong_token_is_explained(win, backend):
    backend.mode = "unauthorised"
    win.phone_btn.click()
    wait_until(lambda: "UNAUTHORISED" in win.phone.status.text(), what="explained")


def test_helpers():
    assert is_loopback("http://127.0.0.1:8000") and is_loopback("http://localhost")
    assert not is_loopback("https://192.168.1.20:8443")
    assert "never" in describe({"name": "x", "capabilities": []}) and "can: none" in describe(
        {"name": "x"}
    )
