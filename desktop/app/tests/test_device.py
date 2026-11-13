"""The desktop UI against a REAL device agent (server running in a thread)."""

from __future__ import annotations

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


def test_agent_stopping_withdraws_waiting_approvals(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    win.agent.call("files.create", {"path": str(agent_env["ws"] / "z.txt"), "content": "z"}, None)
    current_dialog(win)
    agent_env["thread"].stop()
    wait_until(lambda: not win.agent.is_connected, what="disconnect")
    wait_until(lambda: win._dialog is None, what="dialog withdrawn")
    assert win.system.agent_state.text() == "device agent: not running"
    assert not win.system.start_agent_btn.isHidden()
    win.close()


def test_system_panel_shows_real_machine_values_through_the_agent(qapp, settings, agent_env):
    win = make(qapp, settings, agent_env)
    win.sys_btn.click()
    wait_until(lambda: "Memory" in win.system.text.toPlainText(), what="system text")
    text = win.system.text.toPlainText()
    assert "CPU" in text and "OS" in text and "Disk" in text and "Uptime" in text
    assert win.system.agent_state.text() == "device agent: connected"
    assert win.system.start_agent_btn.isHidden()
    win.close()
