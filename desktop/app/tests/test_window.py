"""The voice-only window: what is on screen, what is hidden, and that the mic label stays honest."""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QListWidget, QTextBrowser

from tests.conftest import wait_until
from ultron_desktop.hologram import HologramView
from ultron_desktop.main_window import MainWindow


def make(settings):
    win = MainWindow(settings, animate=False)
    win.show()
    return win


def test_only_voice_things_are_on_screen(qapp, settings):
    win = make(settings)
    assert isinstance(win.hologram, HologramView) and win.hologram.isVisible()
    # chat, conversation list and text entry are gone: nothing of the kind is on screen, and the
    # only such widgets that exist are the memory panel's (hidden until MEM is clicked)
    inputs = win.findChildren(QLineEdit) + win.findChildren(QListWidget)
    assert not [w for w in inputs if w.isVisible()]
    assert inputs and all(win.memory.isAncestorOf(w) or win.phone.isAncestorOf(w) for w in inputs)
    assert not win.findChildren(QTextBrowser)
    for name in ("chat_view", "input", "send_btn", "conv_list", "memory_btn", "device"):
        assert not hasattr(win, name), name
    assert win.voice_btn.isVisible() and win.mute_btn.isVisible() and win.mic.isVisible()
    assert (
        win.sys_btn.isVisible() and win.sys_btn.width() <= 120
    )  # a compact pill, not a big button
    win.close()


def test_system_panel_is_hidden_until_sys_is_clicked_and_hides_again(qapp, settings, backend):
    win = make(settings)
    assert not win.system.isVisible()
    assert not win._health_timer.isActive()  # nothing is polled while it is closed
    win.sys_btn.click()
    assert win.system.isVisible() and win._health_timer.isActive()
    wait_until(lambda: win.system.backend.text().startswith("backend: ok"), what="backend text")
    assert "cloud" in win.system.cloud.text()
    win.sys_btn.click()
    assert not win.system.isVisible() and not win._health_timer.isActive()
    win.close()


def test_backend_offline_and_unauthorised_are_reported_in_the_panel(qapp, settings, backend):
    backend.health_status = 401
    win = make(settings)
    win.sys_btn.click()
    wait_until(lambda: "UNAUTHORISED" in win.system.backend.text(), what="401 text")
    win.close()


def test_local_only_lives_in_the_panel_and_defaults_off(qapp, settings):
    win = make(settings)
    assert not win.system.local_only.isChecked()
    win.close()


def test_voice_state_drives_the_head_hint_and_buttons(qapp, settings, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # not the real wake-name file
    win = make(settings)
    for state, hint in (
        ("standby", "Say “Friday”"),
        ("listening", "listening"),
        ("muted", "Muted"),
    ):
        win._on_voice_state(state)
        assert win.hologram.state == state and hint in win.hint.text()
    win._on_voice_state("off")
    assert win.hologram.state == "off" and win.voice_btn.text() == "Start voice"
    assert not win.mute_btn.isEnabled()
    win.close()


def test_voice_failure_is_visible_on_the_main_screen(qapp, settings):
    win = make(settings)
    win._on_voice_failed("backend is not running")
    assert "backend is not running" in win.notice.text() and win.notice.isVisible()
    win.close()


def test_stylesheet_does_not_paint_over_the_head(qapp, settings):
    win = make(settings)
    img = win.grab().toImage()
    # away from the head the window is the hologram's own deep navy, not a flat widget colour
    r, g, b, _ = img.pixelColor(img.width() // 12, img.height() // 2).getRgb()
    assert b > r and b >= g and max(r, g, b) < 120
    win.close()


# ---------------------------------------------------------------- HUD, shortcuts, overlay
def test_keyboard_shortcuts_match_the_keycaps_on_screen(qapp, settings):
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415

    win = make(settings)
    keys = {w.text(): None for w in win.findChildren(QLabel) if w.objectName() == "key"}
    assert set(keys) == {"V", "M", "N", "S", "R", "P"}
    assert sorted(sc.key().toString() for sc in win._keys) == ["M", "N", "P", "R", "S", "V"]
    win.close()


def test_s_toggles_the_system_panel_and_m_is_ignored_while_voice_is_off(qapp, settings):
    win = make(settings)
    s_key, m_key, v_key = (
        next(sc for sc in win._keys if sc.key().toString() == k) for k in ("S", "M", "V")
    )
    s_key.activated.emit()
    assert win.sys_btn.isChecked() and win.system.isVisible()
    s_key.activated.emit()
    assert not win.sys_btn.isChecked() and not win.system.isVisible()
    clicks = []
    win.mute_btn.clicked.connect(lambda: clicks.append(1))
    m_key.activated.emit()  # voice is off: the mute button is disabled, so the key does nothing
    assert clicks == [] and not win.mute_btn.isEnabled()
    win._on_voice_state("standby")
    m_key.activated.emit()
    assert clicks == [1]
    started = []
    win.voice_btn.clicked.connect(lambda: started.append(1))
    v_key.activated.emit()
    assert started == [1]  # V is the same action as the button, nothing more
    win.close()


def test_hud_layout_title_top_left_buttons_bottom_right(qapp, settings):
    win = make(settings)
    qapp.processEvents()
    w, h = win.width(), win.height()

    def pos(widget):
        p = widget.mapTo(win, widget.rect().center())
        return p.x() / w, p.y() / h

    assert pos(win.findChild(type(win.hint), "wordmark"))[0] < 0.25  # title: left
    assert pos(win.findChild(type(win.hint), "wordmark"))[1] < 0.15  # and at the top
    assert pos(win.sys_btn)[0] > 0.85 and pos(win.sys_btn)[1] < 0.15  # SYS: top right
    for b in (win.voice_btn, win.mute_btn):
        assert pos(b)[0] > 0.75 and pos(b)[1] > 0.8  # voice buttons: bottom right
    win.close()


def test_overlay_darkens_the_edges_and_leaves_the_centre_alone():
    from PySide6.QtCore import QSize  # noqa: PLC0415

    from ultron_desktop.hologram import bake_overlay  # noqa: PLC0415

    img = bake_overlay(QSize(400, 300))
    corner = img.pixelColor(2, 2).alpha()
    centre_rows = [img.pixelColor(200, y).alpha() for y in range(148, 156)]
    assert corner > 120  # a strong vignette in the corners
    assert max(centre_rows) < 60  # only faint scanlines in the middle
    rows = [img.pixelColor(200, y).alpha() for y in range(100, 108)]
    assert max(rows) > min(rows)  # scanlines: alternate rows differ


def test_the_open_system_panel_never_covers_the_sys_button(qapp, settings):
    win = make(settings)
    win.sys_btn.setChecked(True)
    qapp.processEvents()
    centre = win.sys_btn.mapTo(win, win.sys_btn.rect().center())
    assert win.childAt(centre) is win.sys_btn  # the button is what you hit, so you can close it
    assert (
        win.system.geometry().top() >= 0
        and win.system.mapTo(win, win.system.rect().topLeft()).y()
        > win.sys_btn.mapTo(win, win.sys_btn.rect().bottomLeft()).y()
    )
    win.close()


def test_wake_name_picker_saves_the_word_and_changes_the_hint(
    qapp, settings, monkeypatch, tmp_path
):
    from ultron_desktop.settings import read_wake_name  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    win = make(settings)
    assert read_wake_name() == "friday" and win.system.wake.currentText() == "Friday"
    win.system.wake.setCurrentText("Jarvis")
    assert read_wake_name() == "jarvis"
    assert (tmp_path / "ultron" / "wake_name").read_text().strip() == "jarvis"
    win._on_voice_state("standby")
    assert "Say “Jarvis”" in win.hint.text()
    win.close()


def test_wake_name_must_be_one_plain_word(tmp_path, monkeypatch):
    import pytest  # noqa: PLC0415

    from ultron_desktop.settings import write_wake_name  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for bad in ("", "a", "two words", "x/../y", "toolongwakename", "ultron;rm"):
        with pytest.raises(ValueError):
            write_wake_name(bad)
    assert not (tmp_path / "ultron").exists()
