"""A stdlib stand-in for the backend's memory API (all HTTP methods), recording what it receives.

It mirrors the real contract in backend/app/api/v1/memory.py closely enough to drive the panel;
tests/ run the real panel against the REAL backend separately (see docs/PHASE_4_MEMORY.md).
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PHRASE = "delete-all-memories"


def _mem(content, status="confirmed", kind="semantic", **kw):
    return {
        "id": str(uuid.uuid4()), "kind": kind, "content": content, "status": status,
        "source": "api", "privacy": kw.get("privacy", "normal"), "project_id": None,
        "source_conversation_id": None, "embedded": kw.get("embedded", True),
        "created_at": "2026-11-11T10:00:00+00:00", "updated_at": "2026-11-11T10:00:00+00:00",
        "last_used_at": None, "use_count": 0, "expires_at": kw.get("expires_at"),
    }  # fmt: skip


class FakeMemoryBackend:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.requests: list[dict] = []  # method, path, auth header, json body
        self.mode = "normal"  # normal | disabled | unauthorised
        self.conversations = [{"id": "c" * 8 + "-0000-0000-0000-" + "0" * 12}]
        self.embedder_available = True
        self._srv: ThreadingHTTPServer | None = None
        self.port = 0

    def add(self, content, **kw):
        m = _mem(content, **kw)
        self.items.append(m)
        return m

    # ---- server
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
                outer.requests.append({"method": self.command, "path": self.path,
                                       "auth": self.headers.get("Authorization"), "body": body})  # fmt: skip
                if outer.mode == "unauthorised":
                    return self._send(401, {"error": {"code": "unauthorized", "message": "no"}})
                if outer.mode == "disabled":
                    return self._send(503, {"error": {"code": "memory_disabled", "message": "off"}})
                url = urlparse(self.path)
                qs = parse_qs(url.query)
                parts = url.path.strip("/").split("/")
                if self.command == "GET" and url.path == "/v1/memory/status":
                    counts = {
                        s: sum(m["status"] == s for m in outer.items)
                        for s in ("confirmed", "proposed")
                    }
                    return self._send(200, {"enabled": True, "counts": counts,
                        "embedder": {"model": "nomic-embed-text", "available": outer.embedder_available}})  # fmt: skip
                if self.command == "GET" and url.path == "/v1/memories":
                    rows = outer.items
                    if "status" in qs:
                        rows = [m for m in rows if m["status"] == qs["status"][0]]
                    if "q" in qs:
                        rows = [m for m in rows if qs["q"][0].lower() in m["content"].lower()]
                    return self._send(200, {"total": len(rows), "items": rows})
                if self.command == "POST" and url.path == "/v1/memories":
                    text = (body or {}).get("content", "")
                    if "password" in text.lower():
                        return self._send(422, {"error": {"code": "memory_contains_secret",
                            "message": "That looks like a credential, so it was not saved."}})  # fmt: skip
                    dup = next((m for m in outer.items if m["content"] == text), None)
                    if dup:
                        return self._send(
                            201, {"memory": dup, "embedded": True, "duplicate_of": dup["id"]}
                        )
                    m = outer.add(text)
                    return self._send(
                        201,
                        {"memory": m, "embedded": outer.embedder_available, "duplicate_of": None},
                    )
                if self.command == "DELETE" and url.path == "/v1/memories":
                    if qs.get("confirm", [""])[0] != PHRASE:
                        return self._send(
                            422, {"error": {"code": "invalid_memory", "message": "confirm"}}
                        )
                    n_ = len(outer.items)
                    outer.items.clear()
                    return self._send(200, {"deleted": n_})
                if parts[:2] == ["v1", "memories"] and len(parts) >= 3:
                    mid = parts[2]
                    m = next((x for x in outer.items if x["id"] == mid), None)
                    if m is None:
                        return self._send(
                            404, {"error": {"code": "not_found", "message": "No such memory."}}
                        )
                    if self.command == "DELETE" and len(parts) == 3:
                        outer.items.remove(m)
                        return self._send(204)
                    if self.command == "POST" and parts[3:] == ["confirm"]:
                        m["status"] = "confirmed"
                        return self._send(200, m)
                    if self.command == "POST" and parts[3:] == ["reject"]:
                        outer.items.remove(m)
                        return self._send(204)
                if self.command == "GET" and url.path == "/v1/conversations":
                    return self._send(200, outer.conversations[: int(qs.get("limit", ["50"])[0])])
                if self.command == "POST" and url.path.endswith("/extract-memories"):
                    made = [outer.add("Keeps a pet axolotl", status="proposed"),
                            outer.add("Prefers metric units", status="proposed", privacy="local_only")]  # fmt: skip
                    return self._send(200, {"proposals": made, "duplicates": 0, "dropped_secret": 1,
                                            "dropped_invalid": 0, "engine": "local"})  # fmt: skip
                return self._send(404, {"error": {"code": "not_found", "message": "route"}})

            do_GET = do_POST = do_DELETE = do_PATCH = _handle

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
