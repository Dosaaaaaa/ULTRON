"""The desktop UI against a REAL device agent (server running in a thread)."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from tests.conftest import wait_until
from ultron_desktop.main_window import MainWindow
from ultron_desktop.widgets import ApprovalDialog


def make(qapp, settings, agent_env):
    win = MainWindow(settings)
    win.show()
    win.agent.start()
    wait_until(lambda: win.agent.is_connected, what="agent connection")
    return win


def current_dialog(win):
    wait_until(lambda: win._dialog is not None, what="approval dialog")
    return win._dialog


def test_connects_and_lists_roots_and_apps(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    d = win.device
    wait_until(lambda: d.root_box.count() == 2, what="roots")
    assert "read/write" in d.root_box.itemText(0) and "read-only" in d.root_box.itemText(1)
    wait_until(lambda: d.app_list.count() == 1)
    assert "Fake App" in d.app_list.item(0).text()
    assert win.device.start_btn.isHidden() and win.device.tabs.widget(0).isEnabled()
    win.close()


def test_system_tab_shows_real_values(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    win.device.refresh_system()
    wait_until(lambda: "Memory" in win.device.sys_text.toPlainText(), what="system text")
    text = win.device.sys_text.toPlainText()
    assert "CPU" in text and "OS" in text and "Disk" in text and "Uptime" in text
    win.close()


def test_file_search_and_preview(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    d = win.device
    wait_until(lambda: d.root_box.count() == 2)
    d.root_box.setCurrentIndex(1)  # the read-only folder
    d.search_edit.setText("notes")
    d.search()
    wait_until(lambda: d.results.count() == 1, what="search results")
    d.results.setCurrentRow(0)
    wait_until(lambda: "quarterly notes" in d.preview.toPlainText(), what="preview")
    win.close()


def test_launch_allowlisted_app(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    d = win.device
    wait_until(lambda: d.app_list.count() == 1)
    d.app_list.setCurrentRow(0)
    d.launch_selected()
    wait_until(lambda: d.msg.text() != "", what="launch result")
    assert d.msg.text() == "Opened fake"
    win.close()


def test_create_file_requires_approval_and_runs_only_after_approve(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    target = agent_env["ws"] / "made.txt"
    results = []
    win.agent.call("files.create", {"path": str(target), "content": "hi"},
                   lambda ok, d: results.append((ok, d)))  # fmt: skip
    dlg = current_dialog(win)
    assert dlg.approval["level"] == "P2" and str(target) in dlg.approval["summary"]
    assert not target.exists() and not results  # nothing happens before the human decides
    assert dlg.deny_btn.isDefault() and not dlg.approve_btn.isDefault()
    dlg.approve_btn.click()
    wait_until(lambda: results, what="result")
    assert results[0][0] is True and target.read_text() == "hi"
    win.close()


def test_deny_means_nothing_happens(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    target = agent_env["ws"] / "never.txt"
    results = []
    win.agent.call("files.create", {"path": str(target), "content": "x"},
                   lambda ok, d: results.append((ok, d)))  # fmt: skip
    current_dialog(win).deny_btn.click()
    wait_until(lambda: results)
    assert results[0][0] is False and results[0][1]["code"] == "denied" and not target.exists()
    win.close()


def test_closing_the_dialog_window_counts_as_deny(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    target = agent_env["ws"] / "closed.txt"
    results = []
    win.agent.call("files.create", {"path": str(target), "content": "x"},
                   lambda ok, d: results.append((ok, d)))  # fmt: skip
    current_dialog(win).close()
    wait_until(lambda: results)
    assert results[0][0] is False and not target.exists()
    win.close()


def test_delete_needs_exact_typed_phrase(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    victim = agent_env["ws"] / "old.txt"
    victim.write_text("bye")
    results = []
    win.agent.call("files.delete", {"path": str(victim)}, lambda ok, d: results.append((ok, d)))
    dlg = current_dialog(win)
    assert dlg.approval["confirmation_required"] == "delete old.txt"
    assert not dlg.approve_btn.isEnabled()  # disabled until the phrase is typed
    for wrong in ("delete", "delete OLD.txt", "yes"):
        dlg.confirm_edit.setText(wrong)
        assert not dlg.approve_btn.isEnabled()
    dlg.confirm_edit.setText("delete old.txt")
    assert dlg.approve_btn.isEnabled()
    dlg.approve_btn.click()
    wait_until(lambda: results)
    assert results[0][0] is True and not victim.exists()
    assert (agent_env["cfg"].trash_dir / "files" / "old.txt").read_text() == "bye"
    win.close()


def test_multiple_requests_are_shown_one_at_a_time_in_order(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    ws = agent_env["ws"]
    done = []
    for n in ("a", "b", "c"):
        win.agent.call("files.create", {"path": str(ws / f"{n}.txt"), "content": n},
                       lambda ok, d, n=n: done.append((n, ok)))  # fmt: skip
    seen = []
    for _ in range(3):
        dlg = current_dialog(win)
        seen.append(dlg.approval["summary"].split("/")[-1].split(" ")[0])
        assert (
            len(
                [
                    w
                    for w in qapp.topLevelWidgets()
                    if isinstance(w, ApprovalDialog) and w.isVisible()
                ]
            )
            == 1
        )
        dlg.approve_btn.click()
        wait_until(lambda n=len(seen): len(done) == n)
    assert seen == ["a.txt", "b.txt", "c.txt"] and all(ok for _, ok in done)
    win.close()


def test_policy_denials_surface_without_a_dialog(qapp, settings, agent_env, short_tmp):
    win = make(qapp, settings, agent_env)
    results = []
    win.agent.call("files.create", {"path": str(short_tmp / "outside.txt"), "content": "x"},
                   lambda ok, d: results.append((ok, d)))  # fmt: skip
    wait_until(lambda: results)
    assert results[0][0] is False and results[0][1]["code"] == "denied" and win._dialog is None
    win.close()


def test_activity_tab_shows_live_audit_and_verifies_chain(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    d = win.device
    wait_until(lambda: d.app_list.count() == 1)  # app.list call is audited
    wait_until(lambda: d.table.rowCount() >= 1, what="audit rows")
    n = d.table.rowCount()
    d.refresh_system()
    wait_until(lambda: d.table.rowCount() >= n + 3, what="live audit rows")
    tools = {d.table.item(r, 1).text() for r in range(d.table.rowCount())}
    assert {"system.info", "system.usage", "system.storage", "app.list"} <= tools
    next(b for b in d.findChildren(QPushButton) if b.text() == "Verify audit log").click()
    wait_until(lambda: "intact" in d.verify_label.text(), what="verification")
    win.close()


def test_agent_stopping_disables_device_panel_and_clears_dialogs(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    win.agent.call("files.create", {"path": str(agent_env["ws"] / "z.txt"), "content": "z"}, None)
    current_dialog(win)
    agent_env["thread"].stop()
    wait_until(lambda: not win.agent.is_connected, what="disconnect")
    wait_until(lambda: win._dialog is None, what="dialog withdrawn")
    assert win.status.agent.text() == "device agent: not running"
    assert not win.device.tabs.widget(0).isEnabled() and not win.device.start_btn.isHidden()
    win.close()


def test_new_file_dialog_flow_through_the_files_tab(qapp, settings, agent_env, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from ultron_desktop import device_panel

    win = make(qapp, settings, agent_env)
    d = win.device
    wait_until(lambda: d.root_box.count() == 2)
    d.root_box.setCurrentIndex(0)

    class Auto(device_panel.NewFileDialog):
        def exec(self):  # skip the modal loop, answer as a user would
            self.name.setText("from_ui.txt")
            self.body.setPlainText("typed in the UI")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(device_panel, "NewFileDialog", Auto)
    d.new_file()
    current_dialog(win).approve_btn.click()
    wait_until(lambda: (agent_env["ws"] / "from_ui.txt").exists(), what="file creation")
    assert (agent_env["ws"] / "from_ui.txt").read_text() == "typed in the UI"
    win.close()


def test_read_only_folder_blocks_new_file_in_ui(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    d = win.device
    wait_until(lambda: d.root_box.count() == 2)
    d.root_box.setCurrentIndex(1)
    d.new_file()
    assert "read/write" in d.msg.text() and win._dialog is None
    win.close()
