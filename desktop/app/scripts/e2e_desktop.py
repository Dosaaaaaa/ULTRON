"""Live end-to-end: the real desktop window (offscreen) against the REAL backend (uvicorn + real
local model via Ollama) and the REAL device agent process. Nothing is mocked except the display.

The chat turn is sent with "Local only" ticked, so no cloud request is made.

    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/e2e_desktop.py OUT.json
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.conftest import wait_until  # noqa: E402
from ultron_desktop import theme  # noqa: E402
from ultron_desktop.main_window import MainWindow  # noqa: E402
from ultron_desktop.settings import Settings  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
PORT = 18200


def main(out: Path) -> int:
    base = Path(tempfile.mkdtemp(prefix="ued"))
    token = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "HOME": str(base / "home"),
        "XDG_CONFIG_HOME": str(base / "cfg"),
        "XDG_STATE_HOME": str(base / "state"),
        "XDG_DATA_HOME": str(base / "data"),
        "XDG_RUNTIME_DIR": str(base / "run"),
    }
    (base / "home").mkdir()
    (base / "run").mkdir(mode=0o700)
    ws = base / "ws"
    ws.mkdir()
    agent_py = REPO / "desktop/agent/.venv/bin/python"
    subprocess.run(  # noqa: S603
        [str(agent_py), "-m", "ultron_agent", "roots", "add", str(ws), "--write"],
        env=env, check=True, capture_output=True,
    )  # fmt: skip
    backend = subprocess.Popen(  # noqa: S603
        [str(REPO / "backend/.venv/bin/uvicorn"), "app.main:app_factory", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=REPO / "backend",
        env={**os.environ, "ULTRON_API_TOKEN": token, "ULTRON_PORT": str(PORT),
             "ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{base}/e2e.db",
             "ULTRON_ALLOW_CLOUD": "false", "ULTRON_LOG_LEVEL": "WARNING"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    agent = subprocess.Popen(  # noqa: S603
        [str(agent_py), "-m", "ultron_agent", "serve"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    report: dict = {}
    try:
        for _ in range(80):
            try:
                if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        app = QApplication([])
        app.setStyleSheet(theme.QSS)
        s = Settings()
        s.backend_url, s.token = f"http://127.0.0.1:{PORT}", token
        s.agent_runtime_dir = base / "run/ultron"
        s.voice_state_file = base / "voice.state"
        win = MainWindow(s)
        win.show()
        win.start()
        wait_until(lambda: win.agent.is_connected, 15, "agent")
        wait_until(lambda: "qwen3:4b-instruct" in win.status.backend.text(), 15, "health")
        report["status_line"] = win.status.backend.text()

        win.local_only.setChecked(True)
        win.input.setText("What is seventeen times twenty-three? Answer briefly.")
        t0 = time.monotonic()
        win.send()
        wait_until(lambda: "ULTRON\n" in win.chat_view.toPlainText(), 120, "first token")
        report["first_token_s"] = round(time.monotonic() - t0, 2)
        wait_until(lambda: win.send_btn.isEnabled(), 180, "reply end")
        report["total_s"] = round(time.monotonic() - t0, 2)
        report["transcript"] = win.chat_view.toPlainText().strip()
        report["engine_label"] = win.status.engine.text()
        report["privacy_locked_in_ui"] = (not win.local_only.isEnabled(), win.local_only.text())
        h = {"Authorization": f"Bearer {token}"}
        convs = httpx.get(f"http://127.0.0.1:{PORT}/v1/conversations", headers=h).json()
        report["backend_conversations"] = [
            {"messages": c["message_count"], "local_only": c["local_only"]} for c in convs
        ]
        wait_until(lambda: win.conv_list.count() == 1, 10, "conversation list")

        # device agent through the UI: create a file (P2) -> dialog -> approve
        results: list = []
        win.agent.call("files.create", {"path": str(ws / "from_ui.txt"), "content": "e2e"},
                       lambda ok, d: results.append((ok, d)))  # fmt: skip
        wait_until(lambda: win._dialog is not None, 10, "approval dialog")
        report["dialog_summary"] = win._dialog.approval["summary"].replace(str(base), "<tmp>")
        win._dialog.approve_btn.click()
        wait_until(lambda: results, 10, "create result")
        report["file_created"] = (ws / "from_ui.txt").read_text()
        win.device.refresh_system()
        wait_until(lambda: "Memory" in win.device.sys_text.toPlainText(), 10, "system info")
        report["system_text_first_lines"] = win.device.sys_text.toPlainText().splitlines()[:3]
        win.close()
        agent_alive = agent.poll() is None
        report["agent_still_running_after_ui_closed"] = agent_alive
    finally:
        for p in (agent, backend):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
