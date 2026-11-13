"""Device panel: system information, allowlisted apps, approved-folder files, audit activity.

Every action here goes through the device agent's policy engine exactly like any other caller;
the panel has no shortcuts. Risky actions (create/delete) raise the approval dialog.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ultron_desktop.agent_link import AgentLink


def human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return str(n)


def format_system(info: dict, usage: dict, storage: dict) -> str:
    gpu = usage.get("gpu") or info.get("gpu")
    lines = [
        f"OS        {info.get('os')}  (kernel {info.get('kernel')}, {info.get('architecture')})",
        f"CPU       {info.get('cpu_model')}  ·  {info.get('cpu_threads')} threads",
        f"CPU load  {usage.get('cpu_percent')}%   load avg {usage.get('load_average')}",
        f"Memory    {human_bytes(usage.get('memory_used_bytes'))} used of "
        f"{human_bytes(usage.get('memory_total_bytes'))}  ({usage.get('memory_percent')}%)",
        f"Swap      {human_bytes(usage.get('swap_used_bytes'))} used",
        f"Uptime    {int(info.get('uptime_seconds', 0)) // 3600} h "
        f"{int(info.get('uptime_seconds', 0)) % 3600 // 60} min",
    ]
    if gpu:
        lines.append(
            f"GPU       {gpu['name']}  ·  {gpu['memory_used_mib']}/{gpu['memory_total_mib']} MiB"
            f"  ·  {gpu['utilization_percent']}%  ·  {gpu['temperature_c']} °C"
        )
    lines.append("")
    for d in storage.get("disks", []):
        lines.append(
            f"Disk      {d['mount']:<14} {human_bytes(d['used_bytes'])} / "
            f"{human_bytes(d['total_bytes'])}  ({d['used_percent']}%)  {d['filesystem']}"
        )
    return "\n".join(lines)


class NewFileDialog(QDialog):
    def __init__(self, folder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New file")
        lay = QVBoxLayout(self)
        lbl = QLabel(f"Create in: {folder}")
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(lbl)
        self.name = QLineEdit()
        self.name.setPlaceholderText("file name, e.g. notes.txt")
        lay.addWidget(self.name)
        self.body = QPlainTextEdit()
        lay.addWidget(self.body)
        row = QHBoxLayout()
        ok, cancel = QPushButton("Request creation"), QPushButton("Cancel")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)


class DevicePanel(QWidget):
    def __init__(self, agent: AgentLink, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.agent = agent
        self.roots: list[dict] = []
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.state = QLabel("device agent: not connected")
        self.state.setTextFormat(Qt.TextFormat.PlainText)
        self.start_btn = QPushButton("Start agent")
        self.start_btn.clicked.connect(self._start_agent)
        top.addWidget(self.state, 1)
        top.addWidget(self.start_btn)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.msg = QLabel("")
        self.msg.setTextFormat(Qt.TextFormat.PlainText)
        self.msg.setWordWrap(True)
        self.msg.setObjectName("dim")
        lay.addWidget(self.msg)
        self._build_system()
        self._build_apps()
        self._build_files()
        self._build_activity()
        agent.connectedChanged.connect(self._on_connection)
        agent.auditRecord.connect(self._on_audit)
        self._on_connection(agent.is_connected)

    # ------------------------------------------------------------ helpers
    def say(self, text: str, ok: bool = True) -> None:
        self.msg.setText(text)
        self.msg.setStyleSheet("" if ok else "color: #e5534b;")

    def _err(self, e: Any) -> str:
        e = e or {}
        return f"{e.get('code', 'error')}: {e.get('message', '')}".strip(": ")

    def _start_agent(self) -> None:
        if not self.agent.spawn_agent():
            self.say(
                "Cannot start the agent (already started, or its environment is missing).", False
            )

    def _on_connection(self, up: bool) -> None:
        self.state.setText("device agent: connected" if up else "device agent: not running")
        self.start_btn.setVisible(not up)
        for i in range(self.tabs.count()):
            self.tabs.widget(i).setEnabled(up)
        if up:
            self.agent.request("status", self._got_status)
            self.agent.request("audit_tail", self._got_tail, n=50)
            self.refresh_apps()

    def _got_status(self, ok: bool, data: Any) -> None:
        if not ok:
            return
        self.roots = data.get("roots", [])
        self.root_box.clear()
        for r in self.roots:
            self.root_box.addItem(
                f"{r['path']}  ({'read/write' if r['write'] else 'read-only'})", r
            )

    # ------------------------------------------------------------ system tab
    def _build_system(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        self.sys_text = QPlainTextEdit()
        self.sys_text.setObjectName("mono")
        self.sys_text.setReadOnly(True)
        btn = QPushButton("Refresh")
        btn.clicked.connect(self.refresh_system)
        v.addWidget(btn)
        v.addWidget(self.sys_text, 1)
        self.tabs.addTab(w, "System")

    def refresh_system(self) -> None:
        got: dict[str, Any] = {}

        def done(name: str):
            def cb(ok: bool, data: Any) -> None:
                if not ok:
                    self.say(self._err(data), False)
                    return
                got[name] = data
                if len(got) == 3:
                    self.sys_text.setPlainText(
                        format_system(got["info"], got["usage"], got["storage"])
                    )

            return cb

        for name, tool in (("info", "system.info"), ("usage", "system.usage"),
                           ("storage", "system.storage")):  # fmt: skip
            self.agent.call(tool, {}, done(name))

    # ------------------------------------------------------------ apps tab
    def _build_apps(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        self.app_list = QListWidget()
        v.addWidget(self.app_list, 1)
        row = QHBoxLayout()
        self.launch_btn = QPushButton("Open")
        self.launch_btn.setObjectName("primary")
        self.launch_btn.clicked.connect(self.launch_selected)
        hint = QLabel(
            "Only allowlisted apps appear here. Add one with:\n  ultron-agent apps add <id>"
        )
        hint.setObjectName("dim")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(hint, 1)
        row.addWidget(self.launch_btn)
        v.addLayout(row)
        self.tabs.addTab(w, "Apps")

    def refresh_apps(self) -> None:
        def cb(ok: bool, data: Any) -> None:
            self.app_list.clear()
            if not ok:
                return
            for a in data["apps"]:
                item = QListWidgetItem(
                    f"{a['name']}   ({a['id']})" + ("" if a["installed"] else "   [not installed]")
                )
                item.setData(Qt.ItemDataRole.UserRole, a["id"])
                self.app_list.addItem(item)

        self.agent.call("app.list", {}, cb)

    def launch_selected(self) -> None:
        item = self.app_list.currentItem()
        if not item:
            return
        app_id = item.data(Qt.ItemDataRole.UserRole)

        def cb(ok: bool, data: Any) -> None:
            self.say(f"Opened {app_id}" if ok else self._err(data), ok)

        self.agent.call("app.launch", {"app_id": app_id}, cb)

    # ------------------------------------------------------------ files tab
    def _build_files(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        self.root_box = QComboBox()
        v.addWidget(self.root_box)
        row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("file name contains… (or a glob such as *.pdf)")
        self.search_edit.returnPressed.connect(self.search)
        sb = QPushButton("Search")
        sb.clicked.connect(self.search)
        row.addWidget(self.search_edit, 1)
        row.addWidget(sb)
        v.addLayout(row)
        self.results = QListWidget()
        self.results.currentItemChanged.connect(self._preview)
        v.addWidget(self.results, 1)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("mono")
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(130)
        v.addWidget(self.preview)
        row2 = QHBoxLayout()
        self.new_btn = QPushButton("New file…")
        self.new_btn.clicked.connect(self.new_file)
        self.trash_btn = QPushButton("Move to Trash")
        self.trash_btn.clicked.connect(self.trash_selected)
        row2.addStretch(1)
        row2.addWidget(self.new_btn)
        row2.addWidget(self.trash_btn)
        v.addLayout(row2)
        self.tabs.addTab(w, "Files")

    def _root(self) -> dict | None:
        return self.root_box.currentData()

    def search(self) -> None:
        root = self._root()
        pattern = self.search_edit.text().strip()
        if not root or not pattern:
            return

        def cb(ok: bool, data: Any) -> None:
            self.results.clear()
            if not ok:
                self.say(self._err(data), False)
                return
            r = data
            for hit in r["results"]:
                self.results.addItem(hit["path"])
            self.say(f"{len(r['results'])} result(s)" + ("  (truncated)" if r["truncated"] else ""))

        self.agent.call("files.search", {"path": root["path"], "pattern": pattern}, cb)

    def _preview(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return

        def cb(ok: bool, data: Any) -> None:
            self.preview.setPlainText(data["text"] if ok else self._err(data))

        self.agent.call("files.read", {"path": item.text(), "max_bytes": 4096}, cb)

    def new_file(self) -> None:
        root = self._root()
        if not root or not root["write"]:
            self.say(
                "Pick a read/write folder first (add one with: ultron-agent roots add --write).",
                False,
            )
            return
        dlg = NewFileDialog(root["path"], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.name.text().strip()
        if not name or "/" in name:
            self.say("Enter a plain file name (no slashes).", False)
            return

        def cb(ok: bool, data: Any) -> None:
            self.say(f"Created {name}" if ok else self._err(data), ok)

        self.agent.call(
            "files.create",
            {"path": f"{root['path']}/{name}", "content": dlg.body.toPlainText()},
            cb,
        )

    def trash_selected(self) -> None:
        item = self.results.currentItem()
        if not item:
            return

        def cb(ok: bool, data: Any) -> None:
            self.say("Moved to Trash (recoverable)" if ok else self._err(data), ok)
            if ok:
                self.results.takeItem(self.results.row(item))

        self.agent.call("files.delete", {"path": item.text()}, cb)

    # ------------------------------------------------------------ activity tab
    def _build_activity(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Time", "Tool", "Level", "Decision", "Outcome"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.verify_label = QLabel("")
        self.verify_label.setTextFormat(Qt.TextFormat.PlainText)
        vb = QPushButton("Verify audit log")
        vb.clicked.connect(self.verify_log)
        row.addWidget(self.verify_label, 1)
        row.addWidget(vb)
        v.addLayout(row)
        self.tabs.addTab(w, "Activity")

    def _add_row(self, rec: dict) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        vals = [
            time.strftime("%H:%M:%S", time.localtime(rec.get("ts", 0))),
            rec.get("tool", "?"),
            rec.get("level", "-"),
            rec.get("decision", "-"),
            rec.get("outcome", "-") + (f" ({rec['error_code']})" if rec.get("error_code") else ""),
        ]
        for c, v in enumerate(vals):
            self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.table.scrollToBottom()

    def _got_tail(self, ok: bool, data: Any) -> None:
        if ok:
            self.table.setRowCount(0)
            for rec in data:
                self._add_row(rec)

    def _on_audit(self, rec: dict) -> None:
        self._add_row(rec)

    def verify_log(self) -> None:
        def cb(ok: bool, data: Any) -> None:
            if not ok:
                self.verify_label.setText(self._err(data))
            elif data["ok"]:
                self.verify_label.setText(f"audit chain intact ({data['records']} records)")
            else:
                self.verify_label.setText(
                    f"AUDIT LOG TAMPERED at line {data['first_bad_line']}: {data['reason']}"
                )

        self.agent.request("audit_verify", cb)
