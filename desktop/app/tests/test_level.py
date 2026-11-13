"""Speech loudness: file -> controller -> window -> the page. All real code, no microphone."""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import wait_until
from tests.test_voice_ctl import fake_voice
from ultron_desktop.main_window import MainWindow
from ultron_desktop.settings import Settings
from ultron_desktop.voice_ctl import VoiceController, read_level
from ultron_desktop.web_orb import WebOrbView, web_orb_available


def test_read_level_is_forgiving(short_tmp):
    f = short_tmp / "lvl"
    assert read_level(f) == 0.0  # missing
    for text, want in (("0.42\n", 0.42), ("7", 1.0), ("-3", 0.0), ("nan", 0.0), ("inf", 1.0),
                       ("garbage", 0.0), ("", 0.0)):  # fmt: skip
        f.write_text(text)
        assert read_level(f) == want, text


def test_level_file_sits_next_to_the_state_file_and_matches_the_voice_process():
    settings = Settings()  # the real defaults, not the fixture's temp paths
    assert settings.voice_level_file.name == "ultron-voice.level"
    assert settings.voice_level_file.parent == settings.voice_state_file.parent
    cli = Path(__file__).resolve().parents[2] / "voice/ultron_voice/cli.py"
    assert re.search(
        r'"ultron-voice\.state"', cli.read_text()
    )  # the name the suffix is derived from


def test_controller_emits_levels_only_while_speaking(qapp, settings, short_tmp, monkeypatch):
    fake_voice(short_tmp, settings, monkeypatch)
    ctl = VoiceController(settings)
    got: list[float] = []
    ctl.levelChanged.connect(got.append)
    ctl.start()
    try:
        wait_until(lambda: ctl.state == "standby", what="standby")
        settings.voice_level_file.write_text("0.6\n")
        time_before = len(got)
        assert not ctl._level_timer.isActive()  # nothing polled outside speaking
        settings.voice_state_file.write_text("speaking\n")
        wait_until(lambda: ctl.state == "speaking", what="speaking")
        assert ctl._level_timer.isActive()
        wait_until(lambda: 0.6 in got[time_before:], what="level 0.6")
        settings.voice_state_file.write_text("listening\n")
        wait_until(lambda: ctl.state == "listening", what="listening")
        assert not ctl._level_timer.isActive()
        assert got[-1] == 0.0  # drops to silence the moment speech ends
        n = len(got)
        settings.voice_level_file.write_text("0.9\n")
        for _ in range(30):
            qapp.processEvents()
        assert len(got) == n  # a stale file is ignored once not speaking
    finally:
        ctl.stop()


def test_a_new_voice_session_clears_a_stale_level_file(qapp, settings, short_tmp, monkeypatch):
    fake_voice(short_tmp, settings, monkeypatch)
    settings.voice_level_file.write_text("0.8\n")
    ctl = VoiceController(settings)
    ctl.start()
    try:
        assert not settings.voice_level_file.exists()
    finally:
        ctl.stop()


# ------------------------------------------------------------------ into the page
def test_the_native_orb_ignores_levels_without_error(qapp, settings):
    win = MainWindow(settings, animate=False)
    win.show()
    win._on_level(0.7)  # HologramView has no use for it


if web_orb_available():
    from tests.test_web_orb import run_js

    def test_level_reaches_the_page_clamped(qapp):
        v = WebOrbView()
        try:
            wait_until(lambda: v.ready, timeout=20, what="orb ready")
            for sent, shown in ((0.5, "0.5"), (5.0, "1"), (-2.0, "0"), (float("nan"), "0")):
                v.set_level(sent)
                wait_until(
                    lambda shown=shown: run_js(v, "document.body.dataset.level") == shown,
                    timeout=5,
                    what=f"level {shown}",
                )
        finally:
            v.shutdown()

    def test_window_forwards_the_controller_level_to_the_web_orb(qapp, settings, monkeypatch):
        monkeypatch.delenv("ULTRON_ORB", raising=False)
        win = MainWindow(settings, animate=True)
        try:
            assert isinstance(win.hologram, WebOrbView)
            wait_until(lambda: win.hologram.ready, timeout=20, what="orb ready")
            win.voice.levelChanged.emit(0.4)
            wait_until(
                lambda: run_js(win.hologram, "document.body.dataset.level") == "0.4",
                timeout=5,
                what="level forwarded",
            )
        finally:
            win.close()
