"""Render the real window offscreen (real device agent, scripted backend) and save PNGs.

QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/ui_screenshots.py OUT_DIR
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402
from ultron_agent.config import AgentConfig  # noqa: E402

from tests.conftest import AgentThread, FakeBackend, wait_until  # noqa: E402
from ultron_desktop import theme  # noqa: E402
from ultron_desktop.main_window import MainWindow  # noqa: E402
from ultron_desktop.settings import Settings  # noqa: E402


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyleSheet(theme.QSS)
    base = Path(tempfile.mkdtemp(prefix="ush"))
    ws, ro = base / "ws", base / "ro"
    ws.mkdir()
    ro.mkdir()
    (ro / "quarterly-notes.txt").write_text("Q3 notes: revenue flat, churn down 2%.")
    cfg = AgentConfig(base / "conf", base / "state", base / "run")
    cfg.trash_dir = base / "trash"
    cfg.add_root(str(ws), write=True)
    cfg.add_root(str(ro))
    cfg.add_app("calculator", "Calculator", ["/bin/true"])
    agent = AgentThread(cfg)
    agent.start()
    backend = FakeBackend()
    backend.start()
    s = Settings()
    s.backend_url = f"http://127.0.0.1:{backend.port}"
    s.token = "x"
    s.agent_runtime_dir = cfg.runtime_dir
    s.voice_state_file = base / "voice.state"
    win = MainWindow(s)
    win.resize(1320, 780)
    win.show()
    win.start()
    wait_until(lambda: win.conv_list.count() == 2 and win.agent.is_connected)
    win.input.setText("What is seventeen times twenty-three?")
    win.send()
    wait_until(lambda: win.send_btn.isEnabled())
    win.device.refresh_system()
    wait_until(lambda: "Memory" in win.device.sys_text.toPlainText())
    win.status.set_mic("standby")
    win.grab().save(str(out / "ui_main.png"))
    win.status.set_mic("listening")
    win.device.tabs.setCurrentIndex(2)
    wait_until(lambda: win.device.root_box.count() == 2)
    win.device.root_box.setCurrentIndex(1)
    win.device.search_edit.setText("notes")
    win.device.search()
    wait_until(lambda: win.device.results.count() == 1)
    win.device.results.setCurrentRow(0)
    wait_until(lambda: "Q3 notes" in win.device.preview.toPlainText())
    win.grab().save(str(out / "ui_files_mic_live.png"))
    victim = ws / "draft.txt"
    victim.write_text("x")
    win.agent.call("files.delete", {"path": str(victim)}, None)
    wait_until(lambda: win._dialog is not None)
    win._dialog.confirm_edit.setText("delete draft.txt")
    wait_until(lambda: win._dialog.approve_btn.isEnabled())
    win._dialog.grab().save(str(out / "ui_approval_p3.png"))
    win._dialog.deny_btn.click()
    win.agent.call("files.create", {"path": str(ws / "idea.txt"), "content": "hello"}, None)
    wait_until(lambda: win._dialog is not None)
    win._dialog.grab().save(str(out / "ui_approval_p2.png"))
    win._dialog.deny_btn.click()
    win.device.tabs.setCurrentIndex(3)
    wait_until(lambda: win.device.table.rowCount() >= 5)
    win.grab().save(str(out / "ui_activity.png"))
    win.close()
    backend.stop()
    agent.stop()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
