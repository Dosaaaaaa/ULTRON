from __future__ import annotations

import os
import signal
import stat
import time

from tests.conftest import TOKEN, wait_until
from ultron_desktop.main_window import MainWindow
from ultron_desktop.voice_ctl import VoiceController, read_state

FAKE_VOICE = """#!/bin/sh
# Stand-in for `python -m ultron_voice run`: writes the same status file, honours SIGUSR1/SIGTERM.
# It never touches a microphone.
STATE="$FAKE_STATE"
echo "$@" > "$FAKE_ARGS"
pwd > "$FAKE_ARGS.cwd"
env | grep -c "^ULTRON_API_TOKEN=" > "$FAKE_ENVCHK"
echo standby > "$STATE"
[ -n "$FAKE_SAY" ] && printf '%b\n' "$FAKE_SAY"
muted=0
trap 'if [ $muted = 0 ]; then muted=1; echo muted > "$STATE"; else muted=0; echo listening > "$STATE"; fi' USR1
trap 'echo "[NEW CONVERSATION] ${FAKE_NEW:-previous chat erased}"; echo x > "$FAKE_ARGS.new"' USR2
trap 'rm -f "$STATE"; echo terminated > "$FAKE_ARGS.done"; exit 0' TERM
while true; do sleep 0.1; done
"""


def fake_voice(short_tmp, settings, monkeypatch):
    exe = short_tmp / "fake-python"
    exe.write_text(FAKE_VOICE)
    exe.chmod(stat.S_IRWXU)
    settings.voice_python = exe
    monkeypatch.setenv("FAKE_STATE", str(settings.voice_state_file))
    monkeypatch.setenv("FAKE_ARGS", str(short_tmp / "args.txt"))
    monkeypatch.setenv("FAKE_ENVCHK", str(short_tmp / "envchk.txt"))
    return exe


def test_read_state(short_tmp):
    f = short_tmp / "s"
    assert read_state(f) is None
    for label in ("standby", "listening", "thinking", "speaking", "muted"):
        f.write_text(label + "\n")
        assert read_state(f) == label
    f.write_text("garbage\n")
    assert read_state(f) is None


def test_start_mute_stop_lifecycle(qapp, settings, short_tmp, monkeypatch):
    fake_voice(short_tmp, settings, monkeypatch)
    v = VoiceController(settings)
    seen: list[str] = []
    v.stateChanged.connect(seen.append)
    errors: list[str] = []
    v.failed.connect(errors.append)
    v.start(mode="command", local_only=True)
    assert v.state == "starting"
    wait_until(lambda: v.state == "standby", what="status file")
    args = (short_tmp / "args.txt").read_text()
    assert "-m ultron_voice run --mode command --privacy local_only" in args
    assert TOKEN not in args  # not on the command line...
    assert (
        short_tmp / "envchk.txt"
    ).read_text().strip() == "1"  # ...but present in the environment
    v.toggle_mute()
    wait_until(lambda: v.state == "muted")
    v.toggle_mute()
    wait_until(lambda: v.state == "listening")
    v.stop()
    wait_until(lambda: v.state == "off")
    assert (
        short_tmp / "args.txt.done"
    ).read_text().strip() == "terminated"  # got SIGTERM, cleaned up
    assert not settings.voice_state_file.exists() and not v.running
    assert seen[0] == "starting" and seen[-1] == "off"
    assert errors == []  # a requested stop is not a failure
    # started from the voice project directory (the package is not installed into the venv)
    assert (short_tmp / "args.txt.cwd").read_text().strip() == str(settings.voice_dir)


def test_missing_voice_environment_and_token_are_reported(qapp, settings, short_tmp):
    v = VoiceController(settings)
    errors: list[str] = []
    v.failed.connect(errors.append)
    settings.voice_python = short_tmp / "nope"
    v.start()
    assert "not found" in errors[-1] and v.state == "off"
    settings.voice_python = short_tmp / "x"
    (short_tmp / "x").write_text("#!/bin/sh\n")
    settings.token = None
    v.start()
    assert "ULTRON_API_TOKEN" in errors[-1] and v.state == "off"


def test_crash_of_voice_process_returns_to_off(qapp, settings, short_tmp, monkeypatch):
    fake_voice(short_tmp, settings, monkeypatch)
    v = VoiceController(settings)
    v.start()
    wait_until(lambda: v.state == "standby")
    pid = v._proc.processId()
    os.kill(pid, signal.SIGKILL)
    wait_until(lambda: v.state == "off")
    time.sleep(0.05)


def test_window_mic_indicator_follows_state(qapp, settings, short_tmp, monkeypatch):
    fake_voice(short_tmp, settings, monkeypatch)
    win = MainWindow(settings)
    win.show()
    assert win.mic.text() == "MIC OFF" and win.voice_btn.text() == "Start voice"
    win.voice_btn.click()
    wait_until(lambda: win.mic.text().startswith("MIC OPEN"))
    assert "wake word only" in win.mic.text() and win.voice_btn.text() == "Stop voice"
    win.mute_btn.click()
    wait_until(lambda: win.mic.text().startswith("MUTED"))
    assert win.mute_btn.text() == "Unmute"
    win.mute_btn.click()
    wait_until(lambda: win.mic.text() == "● LISTENING")
    assert win.mic.objectName() == "micLive" and "MICROPHONE LIVE" in win.windowTitle()
    win.voice_btn.click()
    wait_until(lambda: win.mic.text() == "MIC OFF")
    assert win.windowTitle() == "ULTRON"
    win.close()


CRASHING_VOICE = """#!/bin/sh
echo "starting speech recognition..."
echo "ModuleNotFoundError: No module named ultron_voice (token $ULTRON_API_TOKEN)" >&2
exit 1
"""


def test_a_voice_process_that_dies_is_reported_with_its_output_and_no_secret(
    qapp, settings, short_tmp
):
    exe = short_tmp / "crash-python"
    exe.write_text(CRASHING_VOICE)
    exe.chmod(stat.S_IRWXU)
    settings.voice_python = exe
    v = VoiceController(settings)
    errors: list[str] = []
    v.failed.connect(errors.append)
    v.start()
    wait_until(lambda: v.state == "off" and bool(errors), what="crash report")
    assert len(errors) == 1
    assert "code 1" in errors[0] and "No module named ultron_voice" in errors[0]
    assert TOKEN not in errors[0] and "***" in errors[0]


def test_unstartable_voice_python_is_reported_once(qapp, settings, short_tmp):
    exe = short_tmp / "not-executable"
    exe.write_text("x")  # exists, but cannot be executed
    settings.voice_python = exe
    v = VoiceController(settings)
    errors: list[str] = []
    v.failed.connect(errors.append)
    v.start()
    wait_until(lambda: bool(errors), what="start failure")
    time.sleep(0.3)
    assert len(errors) == 1 and "could not be started" in errors[0] and v.state == "off"


def test_voice_launch_defaults_point_at_a_real_package_directory():
    from ultron_desktop.settings import Settings  # noqa: PLC0415

    s = Settings()
    assert (s.voice_dir / "ultron_voice" / "__main__.py").exists()
    assert s.voice_python.parents[2] == s.voice_dir  # venv/bin/python -> the same project


NONZERO_ON_TERM = """#!/bin/sh
echo standby > "$FAKE_STATE"
trap 'rm -f "$FAKE_STATE"; exit 143' TERM
while true; do sleep 0.1; done
"""


def test_stopping_a_voice_process_that_exits_nonzero_is_not_reported_as_a_crash(
    qapp, settings, short_tmp, monkeypatch
):
    exe = short_tmp / "nz-python"
    exe.write_text(NONZERO_ON_TERM)
    exe.chmod(stat.S_IRWXU)
    settings.voice_python = exe
    monkeypatch.setenv("FAKE_STATE", str(settings.voice_state_file))
    v = VoiceController(settings)
    errors: list[str] = []
    v.failed.connect(errors.append)
    v.start()
    wait_until(lambda: v.state == "standby")
    v.stop()
    wait_until(lambda: v.state == "off")
    assert errors == []


REFUSED_VOICE = """#!/bin/sh
echo "starting speech recognition..."
echo "ConnectionRefusedError: [Errno 111] Connect call failed ('127.0.0.1', 8000)" >&2
exit 1
"""


def test_backend_not_running_gets_a_plain_explanation(qapp, settings, short_tmp):
    exe = short_tmp / "refused-python"
    exe.write_text(REFUSED_VOICE)
    exe.chmod(stat.S_IRWXU)
    settings.voice_python = exe
    v = VoiceController(settings)
    errors: list[str] = []
    v.failed.connect(errors.append)
    v.start()
    wait_until(lambda: bool(errors), what="report")
    assert len(errors) == 1  # one click, one message
    assert "backend is not running" in errors[0] and "uvicorn" in errors[0]
    assert "Traceback" not in errors[0] and TOKEN not in errors[0]


# ---------------------------------------------------------------- NEW CHAT
def test_new_conversation_signals_the_voice_process_and_reports_the_outcome(
    qapp, settings, short_tmp, monkeypatch
):
    fake_voice(short_tmp, settings, monkeypatch)
    v = VoiceController(settings)
    got: list[tuple[bool, str]] = []
    v.conversationReset.connect(lambda ok, msg: got.append((ok, msg)))
    v.new_conversation()  # not running: nothing to signal, nothing happens
    assert got == []
    v.start()
    try:
        wait_until(lambda: v.state == "standby", what="standby")
        v.new_conversation()
        wait_until(lambda: got, what="outcome")
        assert got == [(True, "previous chat erased")]
        assert (short_tmp / "args.txt.new").exists()  # SIGUSR2 really reached the process
    finally:
        v.stop()


def test_a_chat_that_could_not_be_erased_is_reported_as_not_erased(
    qapp, settings, short_tmp, monkeypatch
):
    fake_voice(short_tmp, settings, monkeypatch)
    monkeypatch.setenv("FAKE_NEW", "previous chat NOT erased (unreachable); it is still stored")
    v = VoiceController(settings)
    got: list[tuple[bool, str]] = []
    v.conversationReset.connect(lambda ok, msg: got.append((ok, msg)))
    v.start()
    try:
        wait_until(lambda: v.state == "standby", what="standby")
        v.new_conversation()
        wait_until(lambda: got, what="outcome")
        assert got[0][0] is False and "NOT erased" in got[0][1]
    finally:
        v.stop()


def test_the_marker_the_window_looks_for_is_the_one_the_voice_process_prints():
    from pathlib import Path

    from ultron_desktop.voice_ctl import NEW_MARKER

    cli = Path(__file__).resolve().parents[2] / "voice/ultron_voice/cli.py"
    assert NEW_MARKER in cli.read_text()


def test_new_chat_button_is_off_until_voice_runs_and_follows_the_state(qapp, settings):
    win = MainWindow(settings, animate=False)
    win.show()
    assert not win.new_btn.isEnabled()
    win._on_voice_state("standby")
    assert win.new_btn.isEnabled()
    win._on_voice_state("speaking")
    assert win.new_btn.isEnabled()  # it can interrupt a reply
    win._on_voice_state("off")
    assert not win.new_btn.isEnabled()
    win.close()


def test_new_chat_click_asks_the_voice_process_and_reports_each_outcome(qapp, settings):
    win = MainWindow(settings, animate=False)
    win.show()
    calls = []
    win.voice.new_conversation = lambda: calls.append(1)
    win._on_voice_state("standby")
    win.new_btn.click()
    assert calls == [1] and "Starting a new chat" in win.notice.text()
    assert not win.new_btn.isEnabled()  # greyed while the erase is in flight
    win._on_reset(True, "previous chat erased")
    assert "Chat erased" in win.notice.text()
    win._on_reset(False, "nothing to erase yet")
    assert "nothing to erase" in win.notice.text() and "NOT erased" not in win.notice.text()
    win._on_reset(False, "previous chat NOT erased (unreachable); it is still stored")
    assert "NOT erased" in win.notice.text() and "still stored" in win.notice.text()
    win.close()


def test_n_key_follows_the_button(qapp, settings):
    win = MainWindow(settings, animate=False)
    win.show()
    calls = []
    win.voice.new_conversation = lambda: calls.append(1)
    key = next(sc for sc in win._keys if sc.key().toString() == "N")
    key.activated.emit()
    assert calls == []  # disabled while voice is off, exactly like the button
    win._on_voice_state("listening")
    key.activated.emit()
    assert calls == [1]
    win.close()
