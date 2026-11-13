"""The design system in the window: one typeface, drawn icons, a header with a close button on every
panel, honest empty and error states, and the controls named for what they do."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFontDatabase

from tests.conftest import wait_until
from tests.fake_devices_backend import FakeDevicesBackend
from ultron_desktop import fonts, icons, theme
from ultron_desktop.main_window import MainWindow
from ultron_desktop.widgets import Captions, ConnectionCard, PanelHeader


def test_the_bundled_typefaces_load_and_are_the_interface_fonts(qapp):
    assert fonts.load()
    families = QFontDatabase.families()
    assert fonts.SANS in families and fonts.MONO in families
    assert fonts.SANS in theme.QSS and fonts.MONO in theme.QSS  # sans for UI, mono only for data
    assert qapp.font().family() == fonts.SANS


def test_every_icon_draws_in_every_state(qapp):
    for name in icons.names():
        ic = icons.icon(name)
        for mode in (ic.Mode.Normal, ic.Mode.Active, ic.Mode.Selected, ic.Mode.Disabled):
            pix = ic.pixmap(18, 18, mode)
            assert not pix.isNull(), (name, mode)
            img = pix.toImage()
            painted = sum(
                1
                for x in range(img.width())
                for y in range(img.height())
                if img.pixelColor(x, y).alpha() > 40
            )
            assert painted > 12, f"{name} is blank in {mode}"  # something was actually drawn


@pytest.fixture
def win(qapp, settings):
    w = MainWindow(settings, animate=False)
    w.show()
    yield w
    w.close()


def test_the_toolbar_buttons_have_icons_labels_tooltips_and_accessible_names(win):
    for button, label in (
        (win.phone_btn, "Phone"),
        (win.mem_btn, "Memory"),
        (win.sys_btn, "System"),
    ):
        assert button.text() == label and not button.icon().isNull()
        assert button.toolTip() and button.accessibleName() == label
        assert button.isCheckable() and button.text() != button.text().upper()  # no shouting labels


def test_the_controls_under_the_orb_say_what_they_do_in_sentence_case(win):
    assert [b.text() for b in (win.voice_btn, win.mute_btn, win.new_btn)] == [
        "Start voice",
        "Mute",
        "New chat",
    ]
    assert win.voice_btn.objectName() == "primary"  # the one main action while voice is off
    win._on_voice_state("standby")
    assert win.voice_btn.text() == "Stop voice" and win.voice_btn.objectName() == "hud"
    win._on_voice_state("off")
    assert win.voice_btn.objectName() == "primary"


def test_each_panel_closes_from_its_own_header_and_unchecks_its_toolbar_button(win):
    for button, panel in (
        (win.phone_btn, win.phone),
        (win.mem_btn, win.memory),
        (win.sys_btn, win.system),
    ):
        button.click()
        assert panel.isVisible() and isinstance(panel.header, PanelHeader)
        panel.header.close_btn.click()
        assert not panel.isVisible() and not button.isChecked()


def test_panel_titles_are_plain_words_not_shouted(win):
    assert [p.header.title.text() for p in (win.phone, win.memory, win.system)] == [
        "Phone",
        "Memory",
        "System",
    ]
    assert win.phone.header.close_btn.accessibleName() == "Close phone"


def test_the_stylesheet_has_a_visible_keyboard_focus_for_every_kind_of_control():
    for selector in (
        "QPushButton:focus",
        "QLineEdit:focus",
        "QListWidget:focus",
        "QPushButton#toolbtn:focus",
        "QPushButton#primary:focus",
    ):
        assert selector in theme.QSS, selector
    for state in ("hover", "pressed", "disabled", "checked"):
        assert f"QPushButton:{state}" in theme.QSS


def test_the_card_explains_an_unreachable_backend_with_the_next_step_and_retry(qapp):
    card = ConnectionCard()
    assert not card.isVisible()
    card.show_problem("unreachable")
    assert "cannot reach its backend" in card.title.text()
    assert "bin/ultron start" in card.cmd.text() and "ultron doctor" in card.body.text()
    card.show_problem("unauthorised")
    assert "API token" in card.body.text() and card.cmd.text() == ""
    asked = []
    card.retryRequested.connect(lambda: asked.append(True))
    card.retry.click()
    assert asked
    card.clear()
    assert not card.isVisible()


def test_the_window_shows_the_card_only_while_the_backend_is_unreachable(qapp, settings):
    settings.backend_url = "http://127.0.0.1:9"  # nothing listens here
    win = MainWindow(settings, animate=False)
    win.show()
    try:
        win.refresh_dots()
        wait_until(lambda: win.problem.isVisible(), what="problem card")
        backend = FakeDevicesBackend()
        backend.start()
        try:
            win.rest.settings.backend_url = f"http://127.0.0.1:{backend.port}"
            win._dots_backend(True, {"engines": [{"reachable": True}]})
            assert not win.problem.isVisible()
        finally:
            backend.stop()
    finally:
        win.close()


def test_an_empty_phone_list_teaches_what_to_do(qapp, settings):
    b = FakeDevicesBackend()
    b.devices = []
    b.start()
    settings.backend_url = f"http://127.0.0.1:{b.port}"
    win = MainWindow(settings, animate=False)
    win.show()
    try:
        win.phone_btn.click()
        wait_until(lambda: win.phone.status.text() != "phones: …", what="loaded")
        assert win.phone.empty.isVisible() and "scan" in win.phone.empty.text().lower()
        assert not win.phone.list.isVisible()
    finally:
        win.close()
        b.stop()


def test_a_long_reply_makes_the_caption_box_taller_instead_of_clipping(qapp):
    c = Captions()
    c.show()
    c.set_you("short question")
    c.set_ultron("A long answer that needs several lines to say. " * 6)
    qapp.processEvents()
    assert c.ultron.minimumHeight() > 2 * c.ultron.fontMetrics().height() - 4
    assert c.minimumHeight() >= c.you.minimumHeight() + c.ultron.minimumHeight()
    c.clear()
    assert c.minimumHeight() <= 72 or not c.isVisible()
