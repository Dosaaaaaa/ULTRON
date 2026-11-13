from __future__ import annotations

import json

from tests.conftest import TOKEN, wait_until
from ultron_desktop.main_window import MainWindow


def make(qapp, settings):
    win = MainWindow(settings)
    win.show()
    return win


def test_streaming_reply_is_rendered_and_conversation_continues(qapp, settings, backend):
    win = make(qapp, settings)
    win.input.setText("What is 17 times 23?")
    win.send()
    assert not win.send_btn.isEnabled() and win.stop_btn.isEnabled()
    wait_until(lambda: win.send_btn.isEnabled(), what="reply to finish")
    text = win.chat_view.toPlainText()
    assert (
        "YOU\nWhat is 17 times 23?" in text
        and "ULTRON\nSeventeen times twenty-three is 391." in text
    )
    assert win.status.engine.text() == "engine: groq/qwen/qwen3.8-27b"
    assert win.conversation_id == "new-conv-1"
    win.input.setText("and 18 times 23?")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled())
    assert backend.frames[1]["conversation_id"] == "new-conv-1"  # same conversation continues
    assert backend.frames[0]["privacy"] == "default" and backend.frames[0]["modality"] == "text"
    win.close()


def test_token_goes_in_header_only_and_is_never_displayed(qapp, settings, backend):
    win = make(qapp, settings)
    win.start()
    win.input.setText("hello")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled())
    wait_until(
        lambda: "ollama" in win.status.backend.text() or "qwen3" in win.status.backend.text()
    )
    assert backend.auth_headers and all(h == f"Bearer {TOKEN}" for h in backend.auth_headers)
    assert all(TOKEN not in p for p in backend.paths)  # never in a URL
    assert all(TOKEN not in json.dumps(f) for f in backend.frames)  # never in a frame
    everything = (
        " ".join(w.text() for w in win.findChildren(type(win.notice)) if hasattr(w, "text"))
        + win.chat_view.toPlainText()
    )
    assert TOKEN not in everything and TOKEN not in repr(settings)
    win.close()


def test_local_only_checkbox_sets_privacy_and_conversation_locks(qapp, settings, backend):
    win = make(qapp, settings)
    win.local_only.setChecked(True)
    win.input.setText("private thing")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled())
    assert backend.frames[0]["privacy"] == "local_only"
    assert not win.local_only.isEnabled() and "locked" in win.local_only.text()
    win.new_conversation()  # a new conversation is unlocked again
    assert win.local_only.isEnabled()
    win.close()


def test_notice_from_server_is_shown(qapp, settings, backend):
    backend.mode = "fallback"
    win = make(qapp, settings)
    win.input.setText("hi")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled())
    assert "answered by the local model" in win.notice.text()
    win.close()


def test_error_frame_is_shown_and_ui_recovers(qapp, settings, backend):
    backend.mode = "error"
    win = make(qapp, settings)
    win.input.setText("hi")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled())
    assert "[error] models_unavailable" in win.chat_view.toPlainText()
    backend.mode = "normal"
    win.input.setText("again")
    win.send()
    wait_until(lambda: "391" in win.chat_view.toPlainText())
    win.close()


def test_unreachable_backend_reports_instead_of_hanging(qapp, settings, backend):
    settings.backend_url = "http://127.0.0.1:1"  # nothing listens here
    win = make(qapp, settings)
    win.input.setText("hi")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled(), what="failure to be reported")
    assert (
        "unreachable" in win.chat_view.toPlainText()
        or "disconnected" in win.chat_view.toPlainText()
    )
    win.close()


def test_stop_sends_cancel_frame(qapp, settings, backend):
    backend.mode = "slow"
    win = make(qapp, settings)
    win.input.setText("long answer please")
    win.send()
    wait_until(lambda: "Seventeen" in win.chat_view.toPlainText())
    win.stop_btn.click()
    wait_until(lambda: any(f["type"] == "cancel" for f in backend.frames), what="cancel frame")
    wait_until(lambda: win.send_btn.isEnabled())
    win.close()


def test_reply_markup_is_shown_literally_not_interpreted(qapp, settings, backend):
    backend.reply = ["<b>bold</b> <img src=x onerror=alert(1)> <a href='http://evil'>link</a>"]
    win = make(qapp, settings)
    win.input.setText("x")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled())
    assert "<b>bold</b> <img src=x onerror=alert(1)>" in win.chat_view.toPlainText()
    assert "<b>" not in win.chat_view.toHtml().replace("&lt;b&gt;", "")  # no real <b> element
    win.close()


def test_conversation_list_history_and_locked_conversations(qapp, settings, backend):
    win = make(qapp, settings)
    win.start()
    wait_until(lambda: win.conv_list.count() == 2, what="conversation list")
    assert win.conv_list.item(1).text().startswith("🔒")
    win.conv_list.itemClicked.emit(win.conv_list.item(0))
    wait_until(lambda: "earlier answer" in win.chat_view.toPlainText())
    assert win.conversation_id == "c1" and win.local_only.isEnabled()
    win.conv_list.itemClicked.emit(win.conv_list.item(1))
    wait_until(lambda: not win.local_only.isEnabled())
    assert win.local_only.isChecked() and win.conversation_id == "c2"
    win.close()


def test_health_labels_and_unauthorised(qapp, settings, backend):
    win = make(qapp, settings)
    win.refresh_health()
    wait_until(lambda: "qwen3:4b-instruct ✓" in win.status.backend.text())
    assert "qwen/qwen3.8-27b ?" in win.status.backend.text()  # cloud is deliberately not probed
    backend.health_status = 401
    win.refresh_health()
    wait_until(lambda: "UNAUTHORISED" in win.status.backend.text())
    win.close()


def test_cloud_budget_label_follows_the_backend(qapp, settings, backend):
    win = make(qapp, settings)
    win.refresh_health()
    wait_until(lambda: "43% of day" in win.status.cloud.text())
    assert "86K/200K" in win.status.cloud.text()
    assert "86000 of 200000" in win.status.cloud.toolTip()
    backend.budget = {**backend.budget, "cooldown_s": 300}
    win.refresh_health()
    wait_until(lambda: "PAUSED 5m" in win.status.cloud.text())
    assert "local" in win.status.cloud.text()  # says what happens instead, not just "paused"
    backend.budget = None
    win.refresh_health()
    wait_until(lambda: "not tracked" in win.status.cloud.text())
    win.close()


def test_memory_button_is_disabled_and_honest(qapp, settings):
    win = make(qapp, settings)
    assert not win.memory_btn.isEnabled() and "Phase 4" in win.memory_btn.text()
    win.close()
