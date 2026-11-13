"""Captions (what was said and answered), the status dots, and the pairing QR in the TEL panel."""

from __future__ import annotations

import pytest

from tests.conftest import wait_until
from tests.fake_devices_backend import FP, FakeDevicesBackend
from tests.test_voice_ctl import fake_voice
from ultron_desktop import phone_panel
from ultron_desktop.main_window import MainWindow
from ultron_desktop.phone_panel import pairing_link, phone_url, qr_pixmap
from ultron_desktop.voice_ctl import VoiceController
from ultron_desktop.widgets import Captions


def test_captions_show_what_was_said_and_answered_and_clear(qapp):
    c = Captions()
    assert not c.isVisible() or c.you.text() == ""
    c.set_you("what is the capital of France")
    assert c.you.text() == "YOU   what is the capital of France" and c.ultron.text() == ""
    c.set_ultron("Paris.")
    assert c.ultron.text() == "ULTRON   Paris."
    c.set_you("and of Spain")  # a new question: the old answer must go
    assert c.ultron.text() == ""
    c.clear()
    assert c.you.text() == "" and c.ultron.text() == ""


def test_captions_are_plain_text_and_clipped(qapp):
    from PySide6.QtCore import Qt

    c = Captions()
    c.set_ultron("<b>bold</b> <img src=x> " + "word " * 200)
    assert c.ultron.textFormat() == Qt.TextFormat.PlainText
    assert "<b>" in c.ultron.text()  # shown literally, never interpreted
    assert len(c.ultron.text()) <= len(
        "ULTRON   "
    ) + Captions.MAX_CHARS and c.ultron.text().endswith("…")


def test_the_controller_turns_console_lines_into_transcript_signals(
    qapp, settings, short_tmp, monkeypatch
):
    fake_voice(short_tmp, settings, monkeypatch)
    monkeypatch.setenv(
        "FAKE_SAY", "  you: hello there\\n  reply: Good evening, operator.\\n  timing: {}"
    )
    v = VoiceController(settings)
    heard, replied = [], []
    v.heard.connect(heard.append)
    v.replied.connect(replied.append)
    v.start()
    try:
        wait_until(lambda: heard and replied, what="transcript lines")
        assert heard == ["hello there"] and replied == ["Good evening, operator."]
    finally:
        v.stop()


def test_the_window_shows_the_exchange_and_new_chat_wipes_it(
    qapp, settings, short_tmp, monkeypatch
):
    fake_voice(short_tmp, settings, monkeypatch)
    monkeypatch.setenv("FAKE_SAY", "  you: remind me later\\n  reply: Set for later.")
    win = MainWindow(settings, animate=False)
    win.show()
    try:
        win.voice_btn.click()
        wait_until(lambda: win.captions.ultron.text() != "", what="captions")
        assert (
            "remind me later" in win.captions.you.text()
            and "Set for later." in win.captions.ultron.text()
        )
        assert win.captions.isVisible()
        win._on_reset(True, "previous chat erased")
        assert win.captions.you.text() == "" and not win.captions.isVisible()
    finally:
        win.voice.stop()
        win.close()


def test_the_status_dots_describe_each_part_and_never_claim_more_than_known(qapp):
    from ultron_desktop.widgets import StatusDots

    d = StatusDots()
    assert "not checked yet" in d.toolTip()
    d.set("backend", "ok", "2 of 2 model engines reachable")
    d.set("phone", "warn", "paired but not connected")
    assert (
        "#4cc38a" in d.text() and "#e0b341" in d.text() and "#566170" in d.text()
    )  # ok, warn, off
    assert "backend: 2 of 2 model engines reachable" in d.toolTip()


@pytest.fixture
def devices():
    b = FakeDevicesBackend()
    b.start()
    yield b
    b.stop()


def test_the_window_reports_backend_and_phone_state_in_the_dots(qapp, settings, devices):
    settings.backend_url = f"http://127.0.0.1:{devices.port}"
    win = MainWindow(settings, animate=False)
    win.show()
    try:
        win.refresh_dots()
        wait_until(
            lambda: "1 of 1 paired phone(s) connected" in win.dots.toolTip(), what="phone dot"
        )
    finally:
        win.close()


def test_the_pairing_link_and_the_qr(qapp, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    link = pairing_link("https://192.168.0.112:8443", "ABCD-EFGH", FP)
    parts = urlsplit(link)
    q = parse_qs(parts.query)
    assert parts.scheme == "ultron" and parts.netloc == "pair"
    assert q["u"] == ["https://192.168.0.112:8443"] and q["c"] == ["ABCD-EFGH"] and q["f"] == [FP]
    assert "f=" not in pairing_link("https://x:1", "ABCD-EFGH", None)
    pix = qr_pixmap(link)
    assert not pix.isNull() and pix.width() == pix.height() and pix.width() > 150
    monkeypatch.setenv("ULTRON_PHONE_URL", "https://myhost:9000/")
    assert phone_url("http://127.0.0.1:8000") == "https://myhost:9000"
    monkeypatch.delenv("ULTRON_PHONE_URL")
    assert phone_url("https://10.0.0.5:8443") == "https://10.0.0.5:8443"  # already reachable
    monkeypatch.setattr(phone_panel, "lan_address", lambda: "192.168.0.112")
    assert phone_url("http://127.0.0.1:8000") == "https://192.168.0.112:8443"


def test_the_panel_shows_a_qr_only_when_the_backend_has_a_certificate(qapp, settings, devices):
    settings.backend_url = f"http://127.0.0.1:{devices.port}"
    win = MainWindow(settings, animate=False)
    win.show()
    try:
        win.phone_btn.click()
        wait_until(lambda: win.phone.status.text() != "phones: …", what="loaded")
        win.phone.pair_btn.click()
        wait_until(lambda: win.phone.code.text() != "", what="code")
        assert win.phone.qr.isVisible() and win.phone.link.startswith("ultron://pair?")
        devices.fingerprint = None
        win.phone.pair_btn.click()
        wait_until(lambda: not win.phone.qr.isVisible(), what="qr hidden without TLS")
        assert win.phone.link == "" and "not served over TLS" in win.phone.how.text()
    finally:
        win.close()
