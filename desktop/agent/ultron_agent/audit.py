"""Append-only, hash-chained audit log (JSON lines, mode 0600).

Each record carries the hash of the previous one, so editing or deleting a record in the middle is
detectable by `verify()`. Truncating the END of the file is not detectable without an external
anchor; this is documented in docs/PHASE_3_DESKTOP.md.

Records never contain file contents or secrets: tools describe their arguments through
`ToolSpec.audit_args`, which replaces payloads with sizes and digests.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(prev: str, record: dict[str, Any]) -> str:
    body = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev + body).encode()).hexdigest()


@dataclass
class VerifyResult:
    ok: bool
    records: int
    first_bad_line: int | None = None
    reason: str = ""


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        path.chmod(0o600)
        self._seq, self._last = self._tail_state()

    def _tail_state(self) -> tuple[int, str]:
        last, seq = GENESIS, 0
        for line in self.path.read_text().splitlines():
            try:
                rec = json.loads(line)
                last, seq = rec["hash"], rec["seq"]
            except (json.JSONDecodeError, KeyError):
                continue
        return seq, last

    def write(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            rec = {"seq": self._seq, "ts": round(time.time(), 3), "prev": self._last, **fields}
            rec["hash"] = _digest(self._last, {k: v for k, v in rec.items() if k != "hash"})
            line = json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            self._last = rec["hash"]
            return rec

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in self.path.read_text().splitlines()[-max(1, min(n, 500)) :]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"corrupt_line": True})
        return out

    def verify(self) -> VerifyResult:
        prev, count = GENESIS, 0
        for i, line in enumerate(self.path.read_text().splitlines(), 1):
            try:
                rec = json.loads(line)
                claimed = rec.pop("hash")
            except (json.JSONDecodeError, KeyError):
                return VerifyResult(False, count, i, "unparseable record")
            if rec.get("prev") != prev:
                return VerifyResult(False, count, i, "chain broken (record removed or reordered)")
            if _digest(prev, rec) != claimed:
                return VerifyResult(False, count, i, "record content was modified")
            prev, count = claimed, count + 1
        return VerifyResult(True, count)
