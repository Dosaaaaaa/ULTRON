"""Backend access: REST (health, conversations) and the streaming chat WebSocket.

The bearer token is attached as a header on each request; it never appears in a URL or the UI.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from ultron_desktop.settings import Settings


class RestClient(QObject):
    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._nam = QNetworkAccessManager(self)

    def _request(self, path: str, *, timeout_ms: int = 8000) -> QNetworkRequest:
        req = QNetworkRequest(QUrl(self.settings.backend_url + path))
        if self.settings.token:
            req.setRawHeader(b"Authorization", f"Bearer {self.settings.token}".encode())
        req.setTransferTimeout(timeout_ms)
        return req

    def send_json(
        self,
        method: str,
        path: str,
        body: dict | None,
        callback: Callable[[bool, Any], None],
        *,
        timeout_ms: int = 8000,
    ) -> None:
        """POST / PATCH / PUT with an optional JSON body (the token goes in the header only)."""
        req = self._request(path, timeout_ms=timeout_ms)
        payload = json.dumps(body).encode() if body is not None else b""
        if body is not None:
            req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self._nam.sendCustomRequest(req, method.encode(), payload)
        reply.finished.connect(lambda r=reply: self._done(r, callback))

    def get_json(self, path: str, callback: Callable[[bool, Any], None]) -> None:
        reply = self._nam.get(self._request(path))
        reply.finished.connect(lambda r=reply: self._done(r, callback))

    def delete(self, path: str, callback: Callable[[bool, Any], None]) -> None:
        reply = self._nam.deleteResource(self._request(path))
        reply.finished.connect(lambda r=reply: self._done(r, callback))

    @staticmethod
    def _done(reply: QNetworkReply, callback: Callable[[bool, Any], None]) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        body = bytes(reply.readAll().data())
        err = reply.error()
        reply.deleteLater()
        if err != QNetworkReply.NetworkError.NoError and not status:
            callback(False, {"code": "unreachable", "message": "backend unreachable"})
            return
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = None
        if status and 200 <= int(status) < 300:
            callback(True, data)
            return
        # the backend reports {"error": {"code", "message"}}; older/other bodies use "detail"
        err_body = (data or {}).get("error") if isinstance(data, dict) else None
        err_body = err_body if isinstance(err_body, dict) else {}
        detail = (data or {}).get("detail", "") if isinstance(data, dict) else ""
        callback(
            False,
            {
                "code": f"http_{status}",
                "error_code": err_body.get("code"),
                "message": err_body.get("message") or (detail if isinstance(detail, str) else ""),
            },
        )


class ChatClient(QObject):
    """One streaming turn at a time over /v1/ws/chat (see shared/protocols/chat_ws.md)."""

    started = Signal(dict)  # engine, fallback, degraded, notice, privacy_locked
    delta = Signal(str)
    finished = Signal(dict)  # end frame
    failed = Signal(str, str)  # code, message
    connectionChanged = Signal(bool)  # noqa: N815

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._ws = QWebSocket(parent=self)
        self._pending: dict[str, Any] | None = None
        self.busy = False
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_text)
        self._ws.errorOccurred.connect(self._on_error)

    @property
    def is_open(self) -> bool:
        from PySide6.QtNetwork import QAbstractSocket

        return self._ws.state() == QAbstractSocket.SocketState.ConnectedState

    def send(
        self, message: str, conversation_id: str | None = None, local_only: bool = False
    ) -> None:
        if self.busy:
            self.failed.emit("busy", "a reply is still being generated")
            return
        frame: dict[str, Any] = {
            "type": "chat",
            "message": message,
            "privacy": "local_only" if local_only else "default",
            "modality": "text",
        }
        if conversation_id:
            frame["conversation_id"] = conversation_id
        self.busy = True
        if self.is_open:
            self._ws.sendTextMessage(json.dumps(frame))
            return
        self._pending = frame
        req = QNetworkRequest(QUrl(self.settings.ws_url))
        if self.settings.token:
            req.setRawHeader(b"Authorization", f"Bearer {self.settings.token}".encode())
        self._ws.open(req)

    def cancel(self) -> None:
        if self.busy and self.is_open:
            self._ws.sendTextMessage(json.dumps({"type": "cancel"}))

    def close(self) -> None:
        self._ws.close()

    # ------------------------------------------------------------ socket events
    def _on_connected(self) -> None:
        self.connectionChanged.emit(True)
        if self._pending is not None:
            frame, self._pending = self._pending, None
            self._ws.sendTextMessage(json.dumps(frame))

    def _on_disconnected(self) -> None:
        self.connectionChanged.emit(False)
        if self.busy:
            self.busy = False
            self.failed.emit("disconnected", "connection to the backend was lost")

    def _on_error(self, *_a: object) -> None:
        if self.busy and not self.is_open and self._pending is not None:
            self._pending = None
            self.busy = False
            self.failed.emit("unreachable", "backend unreachable")

    def _on_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError:
            return
        kind = msg.get("type")
        if kind == "start":
            self.started.emit(msg)
        elif kind == "delta":
            self.delta.emit(msg.get("text", ""))
        elif kind == "end":
            self.busy = False
            self.finished.emit(msg)
        elif kind == "error":
            self.busy = False
            self.failed.emit(msg.get("code", "error"), msg.get("message", ""))
