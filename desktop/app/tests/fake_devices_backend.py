"""A stdlib stand-in for the backend's /v1/devices API (real contract: backend/app/api/v1/devices.py)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FP = "D7:E8:7C:C1:7B:C8:9E:B6:5E:40:22:50:28:EB:4A:A8:EE:E2:BD:30:A0:57:41:8A:24:ED:D6:1A:57:05:10:92"


class FakeDevicesBackend:
    def __init__(self) -> None:
        self.devices = [
            {"id": "d1", "name": "Pixel 8", "kind": "phone", "capabilities": ["chat", "notify"],
             "created_at": "2026-11-12T10:00:00+00:00", "last_seen_at": "2026-11-12T10:05:00+00:00",
             "revoked": False, "connected": True},
            {"id": "d2", "name": "Old tablet", "kind": "tablet", "capabilities": ["chat"],
             "created_at": "2026-11-01T10:00:00+00:00", "last_seen_at": None,
             "revoked": True, "connected": False},
        ]  # fmt: skip
        self.requests: list[dict] = []
        self.mode = "normal"  # normal | unauthorised
        self.fingerprint: str | None = FP
        self._srv: ThreadingHTTPServer | None = None
        self.port = 0

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a) -> None:
                pass

            def _send(self, code, body=None):
                raw = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                if raw:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                outer.requests.append(
                    {"method": self.command, "path": self.path, "body": body,
                     "auth": self.headers.get("Authorization")}
                )  # fmt: skip
                if outer.mode == "unauthorised":
                    return self._send(401, {"error": {"code": "unauthorized", "message": "no"}})
                if self.command == "GET" and self.path == "/v1/devices":
                    return self._send(200, outer.devices)
                if self.command == "POST" and self.path == "/v1/devices/pairing-code":
                    return self._send(200, {"code": "ABCD-EFGH", "expires_at": "2026-11-12T10:10:00+00:00",
                                            "capabilities": body["capabilities"],
                                            "tls_fingerprint_sha256": outer.fingerprint})  # fmt: skip
                if self.command == "DELETE" and self.path.startswith("/v1/devices/"):
                    did = self.path.rsplit("/", 1)[1]
                    for d in outer.devices:
                        if d["id"] == did:
                            d["revoked"], d["connected"] = True, False
                    return self._send(204)
                self._send(404, {"error": {"code": "not_found", "message": "x"}})

            do_GET = do_POST = do_DELETE = _handle

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
