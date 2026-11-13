"""The MEM panel: what it shows, what it asks before deleting, and that it stays honest."""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from PySide6.QtWidgets import QLabel

from tests.conftest import TOKEN, wait_until
from tests.fake_memory_backend import PHRASE, FakeMemoryBackend
from ultron_desktop.main_window import MainWindow
from ultron_desktop.memory_panel import describe, explain


@pytest.fixture
def mem_backend():
    b = FakeMemoryBackend()
    b.start()
    yield b
    b.stop()


@pytest.fixture
def win(qapp, settings, mem_backend):
    settings.backend_url = f"http://127.0.0.1:{mem_backend.port}"
    w = MainWindow(settings, animate=False)
    w.show()
    w.memory.ask = lambda text: True  # tests that care about the question override this
    yield w
    w.close()


def open_panel(win):
    win.mem_btn.click()
    wait_until(lambda: win.memory.status.text() != "memory: …", what="status loaded")


def rows(win):
    return [win.memory.list.item(i).text() for i in range(win.memory.list.count())]


# ---------------------------------------------------------------- visibility and layout
def test_panel_is_hidden_until_mem_is_clicked_and_closes_again(win):
    assert not win.memory.isVisible()
    win.mem_btn.click()
    assert win.memory.isVisible()
    win.mem_btn.click()
    assert not win.memory.isVisible()


def test_only_one_panel_is_open_at_a_time(win):
    win.sys_btn.click()
    assert win.system.isVisible()
    win.mem_btn.click()
    assert win.memory.isVisible() and not win.system.isVisible() and not win.sys_btn.isChecked()
    win.sys_btn.click()
    assert win.system.isVisible() and not win.memory.isVisible() and not win.mem_btn.isChecked()


def test_the_open_panel_never_covers_the_buttons_that_close_it(win):
    win.mem_btn.click()
    root = win.centralWidget()
    panel_top = win.memory.mapTo(root, win.memory.rect().topLeft()).y()
    for btn in (win.mem_btn, win.sys_btn):
        btn_bottom = btn.mapTo(root, btn.rect().bottomLeft()).y()
        assert panel_top >= btn_bottom, btn.text()


def test_r_opens_memory_and_the_keycap_is_on_screen(win):
    keys = {w.text() for w in win.findChildren(QLabel) if w.objectName() == "key"}
    assert keys == {"V", "M", "N", "S", "R", "P"}
    assert sorted(sc.key().toString() for sc in win._keys) == ["M", "N", "P", "R", "S", "V"]
    next(sc for sc in win._keys if sc.key().toString() == "R").activated.emit()
    assert win.memory.isVisible()


# ---------------------------------------------------------------- showing memory honestly
def test_lists_everything_with_provenance_tags(win, mem_backend):
    mem_backend.add("Prefers metric units")
    mem_backend.add("Keeps a pet axolotl", status="proposed", kind="episodic")
    mem_backend.add("Sees a therapist on Fridays", privacy="local_only")
    mem_backend.add("Old note", expires_at="2027-01-31T00:00:00+00:00")
    mem_backend.add("Saved during an outage", embedded=False)
    open_panel(win)
    wait_until(lambda: len(rows(win)) == 5, what="five rows")
    text = "\n".join(rows(win))
    assert "[semantic]  Prefers metric units" in text
    assert "AWAITING REVIEW" in text and "local-only" in text
    assert "expires 2027-01-31" in text and "not yet searchable" in text
    assert "4 confirmed · 1 awaiting review" in win.memory.status.text()
    assert "nomic-embed-text (on this machine)" in win.memory.status.text()


def test_an_empty_store_says_so_instead_of_showing_a_blank_list(win):
    open_panel(win)
    wait_until(lambda: "Nothing stored yet" in win.memory.msg.text(), what="empty message")


def test_when_search_is_down_the_status_says_notes_are_not_searchable(win, mem_backend):
    mem_backend.embedder_available = False
    open_panel(win)
    assert "UNAVAILABLE" in win.memory.status.text()


def test_memory_text_is_shown_literally_never_as_markup(win, mem_backend):
    mem_backend.add("<b>bold</b> & <script>alert(1)</script>")
    open_panel(win)
    wait_until(lambda: len(rows(win)) == 1)
    assert rows(win)[0].endswith("<b>bold</b> & <script>alert(1)</script>")


def test_describe_and_explain_helpers():
    assert describe({"kind": "semantic", "content": "x"}) == "[semantic]  x"
    assert explain({"error_code": "memory_disabled"}).startswith("memory is switched off")
    assert explain({"code": "unreachable"}) == "backend unreachable"
    assert "UNAUTHORISED" in explain({"code": "http_401"})
    assert "UNAUTHORISED" in explain({"code": "http_401", "error_code": "unauthorized"})
    assert explain({"error_code": "x", "message": "boom"}) == "x: boom"


@pytest.mark.parametrize(
    ("mode", "needle"),
    [("disabled", "switched off"), ("unauthorised", "UNAUTHORISED")],
)
def test_backend_states_are_reported_plainly(win, mem_backend, mode, needle):
    mem_backend.mode = mode
    win.mem_btn.click()
    wait_until(lambda: needle in win.memory.status.text(), what=mode)


def test_unreachable_backend_is_reported(qapp, settings):
    settings.backend_url = "http://127.0.0.1:9"  # nothing listens here
    w = MainWindow(settings, animate=False)
    w.show()
    try:
        w.mem_btn.click()
        wait_until(lambda: "unreachable" in w.memory.status.text(), what="unreachable")
    finally:
        w.close()


# ---------------------------------------------------------------- acting on memory
def select(win, text):
    wait_until(lambda: any(text in r for r in rows(win)), what=f"row {text}")
    for i, r in enumerate(rows(win)):
        if text in r:
            win.memory.list.setCurrentRow(i)
            return
    raise AssertionError(text)


def test_confirm_is_only_enabled_for_suggestions_and_makes_them_usable(win, mem_backend):
    mem_backend.add("Prefers metric units")
    mem_backend.add("Keeps a pet axolotl", status="proposed")
    open_panel(win)
    select(win, "metric")
    assert not win.memory.confirm_btn.isEnabled() and win.memory.delete_btn.text() == "Delete"
    select(win, "axolotl")
    assert win.memory.confirm_btn.isEnabled() and win.memory.delete_btn.text() == "Reject"
    win.memory.confirm_btn.click()
    wait_until(lambda: all(m["status"] == "confirmed" for m in mem_backend.items), what="confirmed")
    wait_until(lambda: "AWAITING REVIEW" not in "".join(rows(win)), what="row refreshed")


def test_delete_asks_first_and_does_nothing_if_you_say_no(win, mem_backend):
    mem_backend.add("Keep me")
    open_panel(win)
    select(win, "Keep me")
    asked = []
    win.memory.ask = lambda text: asked.append(text) or False
    win.memory.delete_btn.click()
    assert asked and "Keep me" in asked[0]
    assert [r for r in mem_backend.requests if r["method"] == "DELETE"] == []
    assert len(mem_backend.items) == 1


def test_delete_removes_it_when_confirmed(win, mem_backend):
    mem_backend.add("Remove me")
    mem_backend.add("Stay")
    open_panel(win)
    select(win, "Remove me")
    win.memory.delete_btn.click()
    wait_until(lambda: [m["content"] for m in mem_backend.items] == ["Stay"], what="deleted")
    wait_until(lambda: len(rows(win)) == 1 and "Stay" in rows(win)[0], what="list refreshed")


def test_rejecting_a_suggestion_uses_the_reject_endpoint(win, mem_backend):
    mem_backend.add("Maybe true", status="proposed")
    open_panel(win)
    select(win, "Maybe true")
    win.memory.delete_btn.click()
    wait_until(lambda: not mem_backend.items, what="rejected")
    assert any(r["path"].endswith("/reject") for r in mem_backend.requests)


def test_remember_adds_a_note_and_clears_the_box(win, mem_backend):
    open_panel(win)
    win.memory.add_edit.setText("  I take two sugars  ")
    win.memory.add_btn.click()
    wait_until(
        lambda: [m["content"] for m in mem_backend.items] == ["I take two sugars"], what="added"
    )
    wait_until(lambda: win.memory.add_edit.text() == "", what="box cleared after the reply")
    assert "Remembered" in win.memory.msg.text()
    sent = next(r for r in mem_backend.requests if r["method"] == "POST")
    assert sent["body"] == {"content": "I take two sugars"}


def test_remember_ignores_blank_input_and_reports_repeats_and_refusals(win, mem_backend):
    open_panel(win)
    win.memory.add_edit.setText("   ")
    win.memory.add_btn.click()
    assert not [r for r in mem_backend.requests if r["method"] == "POST"]
    mem_backend.add("Already here")
    win.memory.add_edit.setText("Already here")
    win.memory.add_btn.click()
    wait_until(lambda: "Already stored" in win.memory.msg.text(), what="duplicate note")
    win.memory.add_edit.setText("my password is hunter2hunter2")
    win.memory.add_btn.click()
    wait_until(lambda: "credential" in win.memory.msg.text(), what="refusal shown")
    assert win.memory.add_edit.text() != ""  # the text stays so you can see what was refused


def test_suggest_reads_the_latest_conversation_and_shows_suggestions_for_review(win, mem_backend):
    open_panel(win)
    win.memory.suggest_btn.click()
    assert not win.memory.suggest_btn.isEnabled()  # busy while the local model works
    wait_until(lambda: "2 suggestions added" in win.memory.msg.text(), what="suggestions")
    assert "1 looked like a secret" in win.memory.msg.text()
    assert win.memory.suggest_btn.isEnabled()
    wait_until(
        lambda: len(rows(win)) == 2 and all("AWAITING REVIEW" in r for r in rows(win)),
        what="review view",
    )
    paths = [(r["method"], r["path"].split("?")[0]) for r in mem_backend.requests]
    assert ("GET", "/v1/conversations") in paths
    assert any(m == "POST" and p.endswith("/extract-memories") for m, p in paths)


def test_suggest_with_no_conversation_says_so(win, mem_backend):
    mem_backend.conversations = []
    open_panel(win)
    win.memory.suggest_btn.click()
    wait_until(lambda: "no conversation" in win.memory.msg.text(), what="no conversation")
    assert win.memory.suggest_btn.isEnabled()


def test_delete_all_needs_a_yes_and_sends_the_confirmation_phrase(win, mem_backend):
    mem_backend.add("a note one")
    mem_backend.add("a note two")
    open_panel(win)
    wait_until(lambda: len(rows(win)) == 2)
    win.memory.ask = lambda text: False
    win.memory.clear_btn.click()
    assert len(mem_backend.items) == 2 and not [
        r for r in mem_backend.requests if r["method"] == "DELETE"
    ]
    win.memory.ask = lambda text: True
    win.memory.clear_btn.click()
    wait_until(lambda: not mem_backend.items, what="all deleted")
    sent = next(r for r in mem_backend.requests if r["method"] == "DELETE")
    assert f"confirm={PHRASE}" in sent["path"]
    wait_until(
        lambda: "Deleted 2 memories" in win.memory.msg.text() or not rows(win), what="message"
    )


def test_the_dialog_defaults_to_no_so_enter_cannot_delete(qapp, settings, mem_backend):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    settings.backend_url = f"http://127.0.0.1:{mem_backend.port}"
    w = MainWindow(settings, animate=False)
    w.show()
    seen = {}

    def peek():
        box = next(
            b for b in qapp.topLevelWidgets() if isinstance(b, QMessageBox) and b.isVisible()
        )
        seen["default"] = box.defaultButton().text().replace("&", "")
        box.reject()

    QTimer.singleShot(200, peek)
    assert w.memory._ask("Delete everything?") is False
    assert seen["default"] == "No"
    w.close()


def test_filter_text_is_sent_url_encoded(win, mem_backend):
    mem_backend.add("tea & biscuits")
    open_panel(win)
    win.memory.filter.setText("tea & b")
    win.memory.refresh()
    wait_until(
        lambda: any("q=" in r["path"] for r in mem_backend.requests), what="filtered request"
    )
    q_req = next(r for r in mem_backend.requests if "q=" in r["path"])
    assert unquote(q_req["path"].split("q=")[1].split("&")[0]) == "tea & b"
    assert "tea & b" not in q_req["path"]  # it was encoded on the wire


def test_the_token_is_only_ever_sent_as_a_header(win, mem_backend):
    mem_backend.add("a note")
    open_panel(win)
    win.memory.add_edit.setText("another note")
    win.memory.add_btn.click()
    wait_until(lambda: len(mem_backend.items) == 2)
    assert mem_backend.requests
    for r in mem_backend.requests:
        assert r["auth"] == f"Bearer {TOKEN}"
        assert TOKEN not in r["path"] and TOKEN not in str(r["body"])
    assert TOKEN not in win.memory.msg.text() and TOKEN not in win.memory.status.text()
