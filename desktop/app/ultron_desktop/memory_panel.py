"""The memory display. Hidden until MEM is clicked.

Everything ULTRON remembers is listed here, and nothing is hidden from you: you can read it, add
to it, correct it by deleting and re-adding, and delete any of it. Notes a model only *suggested*
are marked AWAITING REVIEW; ULTRON does not use them until you confirm them. The panel only talks
to the backend's /v1/memories API; it never reads the database itself.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ultron_desktop.backend_link import RestClient
from ultron_desktop.widgets import ElidedLabel, PanelHeader

DELETE_ALL_PHRASE = "delete-all-memories"  # must match the backend's confirmation phrase
VIEWS = (("All", None), ("Confirmed", "confirmed"), ("Awaiting review", "proposed"))


def describe(item: dict) -> str:
    """One list row. Plain text only: memory content is never interpreted as markup."""
    tags = []
    if item.get("status") == "proposed":
        tags.append("AWAITING REVIEW")
    if item.get("privacy") == "local_only":
        tags.append("local-only")
    if item.get("expires_at"):
        tags.append("expires " + item["expires_at"][:10])
    if not item.get("embedded", True):
        tags.append("not yet searchable")
    suffix = ("   ·  " + " · ".join(tags)) if tags else ""
    return f"[{item.get('kind')}]  {item.get('content')}{suffix}"


def explain(data: Any) -> str:
    e = data or {}
    code = e.get("error_code") or e.get("code") or "error"
    if e.get("code") == "http_401" or code == "unauthorized":
        return "UNAUTHORISED (check ULTRON_API_TOKEN)"
    if code == "memory_disabled":
        return "memory is switched off in the backend (ULTRON_MEMORY_ENABLED=false)"
    if code == "unreachable":
        return "backend unreachable"
    return f"{code}: {e.get('message', '')}".strip(": ")


class MemoryPanel(QFrame):
    def __init__(self, rest: RestClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rest = rest
        self.setObjectName("panel")
        self.setFixedWidth(560)
        self._items: dict[str, dict] = {}
        self.ask = self._ask  # tests replace this; it must return True only on an explicit yes

        lay = QVBoxLayout(self)
        self.header = PanelHeader("Memory")
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.header.add_action(self.refresh_btn)
        lay.addWidget(self.header)

        self.status = ElidedLabel("memory: …")
        lay.addWidget(self.status)

        filt = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter text")
        self.filter.setClearButtonEnabled(True)
        self.filter.returnPressed.connect(self.refresh)
        self.view = QComboBox()
        for label, _ in VIEWS:
            self.view.addItem(label)
        self.view.currentIndexChanged.connect(lambda _i: self.refresh())
        filt.addWidget(self.filter, 1)
        filt.addWidget(self.view)
        lay.addLayout(filt)

        self.list = QListWidget()
        self.list.setMinimumHeight(230)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.confirm_btn = QPushButton("Confirm")
        self.confirm_btn.setToolTip("Accept a suggested note: ULTRON will then use it")
        self.delete_btn = QPushButton("Delete")
        self.confirm_btn.clicked.connect(self._confirm)
        self.delete_btn.clicked.connect(self._delete)
        self.suggest_btn = QPushButton("Suggest from last chat")
        self.suggest_btn.setToolTip(
            "Ask the LOCAL model for notes worth keeping from the latest conversation. They "
            "arrive as suggestions; nothing is used until you confirm it. Never uses the cloud."
        )
        self.suggest_btn.clicked.connect(self._suggest)
        row.addWidget(self.confirm_btn)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        row.addWidget(self.suggest_btn)
        lay.addLayout(row)

        add = QHBoxLayout()
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("Remember something…  (no passwords or keys)")
        self.add_edit.setMaxLength(600)
        self.add_edit.returnPressed.connect(self._add)
        self.add_btn = QPushButton("Remember")
        self.add_btn.clicked.connect(self._add)
        self.clear_btn = QPushButton("Delete all…")
        self.clear_btn.clicked.connect(self._clear_all)
        add.addWidget(self.add_edit, 1)
        add.addWidget(self.add_btn)
        add.addWidget(self.clear_btn)
        lay.addLayout(add)

        self.msg = QLabel("")
        self.msg.setObjectName("note")
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.msg)
        self._sync_buttons()

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        self.rest.get_json("/v1/memory/status", lambda ok, d: self._on_status(ok, d))
        status = VIEWS[self.view.currentIndex()][1]
        q = self.filter.text().strip()
        params = ["limit=200"]
        if status:
            params.append(f"status={status}")
        if q:
            from urllib.parse import quote  # noqa: PLC0415

            params.append("q=" + quote(q, safe=""))
        self.rest.get_json("/v1/memories?" + "&".join(params), lambda ok, d: self._on_list(ok, d))

    def _on_status(self, ok: bool, data: Any) -> None:
        if not ok:
            self.status.setText("memory: " + explain(data))
            return
        if not data.get("enabled"):
            self.status.setText("memory: switched off in the backend")
            return
        c, emb = data.get("counts", {}), data.get("embedder", {})
        review = c.get("proposed", 0)
        search = (
            f"search: {emb.get('model')} (on this machine)"
            if emb.get("available")
            else "search: UNAVAILABLE (notes are saved but not searchable yet)"
        )
        self.status.setText(
            f"{c.get('confirmed', 0)} confirmed · {review} awaiting review · {search}"
        )

    def _on_list(self, ok: bool, data: Any) -> None:
        if not ok:
            self.msg.setText(explain(data))
            return
        self._items = {m["id"]: m for m in data.get("items", [])}
        self.list.clear()
        for m in data.get("items", []):
            item = QListWidgetItem(describe(m))
            item.setData(Qt.ItemDataRole.UserRole, m["id"])
            self.list.addItem(item)
        total = data.get("total", 0)
        shown = len(self._items)
        self.msg.setText(
            "" if shown else "Nothing stored yet. Say “remember that …” or add a note below."
        )
        if total > shown:
            self.msg.setText(f"Showing {shown} of {total}. Use the filter to narrow it.")
        self._sync_buttons()

    # ------------------------------------------------------------ selection
    def selected(self) -> dict | None:
        items = self.list.selectedItems()
        if not items:
            return None
        return self._items.get(items[0].data(Qt.ItemDataRole.UserRole))

    def _sync_buttons(self) -> None:
        sel = self.selected()
        self.confirm_btn.setEnabled(bool(sel and sel.get("status") == "proposed"))
        self.delete_btn.setEnabled(sel is not None)
        self.delete_btn.setText("Reject" if sel and sel.get("status") == "proposed" else "Delete")

    # ------------------------------------------------------------ actions
    def _ask(self, text: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)  # Enter never deletes by accident
        return box.exec() == QMessageBox.StandardButton.Yes

    def _after(self, what: str):
        def cb(ok: bool, data: Any) -> None:
            self.msg.setText(what if ok else explain(data))
            self.refresh()

        return cb

    def _confirm(self) -> None:
        sel = self.selected()
        if sel and sel.get("status") == "proposed":
            self.rest.send_json(
                "POST", f"/v1/memories/{sel['id']}/confirm", None, self._after("Confirmed.")
            )

    def _delete(self) -> None:
        sel = self.selected()
        if not sel:
            return
        proposed = sel.get("status") == "proposed"
        verb = "Reject this suggestion?" if proposed else "Delete this memory permanently?"
        if not self.ask(f"{verb}\n\n{sel.get('content')}"):
            return
        if proposed:
            self.rest.send_json(
                "POST", f"/v1/memories/{sel['id']}/reject", None, self._after("Rejected.")
            )
        else:
            self.rest.delete(f"/v1/memories/{sel['id']}", self._after("Deleted."))

    def _add(self) -> None:
        text = self.add_edit.text().strip()
        if not text:
            return

        def cb(ok: bool, data: Any) -> None:
            if not ok:
                self.msg.setText(explain(data))
                return
            self.add_edit.clear()
            dup = (data or {}).get("duplicate_of")
            note = "Already stored; nothing new was added." if dup else "Remembered."
            if not (data or {}).get("embedded", True) and not dup:
                note += " (Search is down, so it is not searchable until it returns.)"
            self.msg.setText(note)
            self.refresh()

        self.rest.send_json("POST", "/v1/memories", {"content": text}, cb)

    def _suggest(self) -> None:
        self.msg.setText("Asking the local model… this can take several seconds.")
        self.suggest_btn.setEnabled(False)

        def done(ok: bool, data: Any) -> None:
            self.suggest_btn.setEnabled(True)
            if not ok:
                self.msg.setText(explain(data))
                return
            n = len(data.get("proposals", []))
            dropped = data.get("dropped_secret", 0)
            note = (
                f"{n} suggestion{'s' if n != 1 else ''} added for review."
                if n
                else "Nothing worth keeping was found."
            )
            if dropped:
                note += f" {dropped} looked like a secret and was discarded."
            self.msg.setText(note)
            self.view.setCurrentIndex(2)  # show what needs review

        def got_conversation(ok: bool, data: Any) -> None:
            if not ok:
                self.suggest_btn.setEnabled(True)
                self.msg.setText(explain(data))
                return
            if not data:
                self.suggest_btn.setEnabled(True)
                self.msg.setText("There is no conversation to read yet.")
                return
            self.rest.send_json(
                "POST",
                f"/v1/conversations/{data[0]['id']}/extract-memories",
                None,
                done,
                timeout_ms=120_000,  # a 4B model on a small GPU can need a while
            )

        self.rest.get_json("/v1/conversations?limit=1", got_conversation)

    def _clear_all(self) -> None:
        if not self.ask("Delete EVERY memory, including suggestions? This cannot be undone."):
            return
        self.rest.delete(
            f"/v1/memories?confirm={DELETE_ALL_PHRASE}",
            lambda ok, d: self._after(
                f"Deleted {(d or {}).get('deleted', 0)} memories." if ok else ""
            )(ok, d),
        )
