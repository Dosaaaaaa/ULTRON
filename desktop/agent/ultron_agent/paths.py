"""Path scoping: every file path a tool touches must resolve inside an approved root.

Rules, enforced here and nowhere else (the tools only receive already-checked paths):
- absolute paths only, no NUL bytes, symlinks resolved BEFORE the root check;
- a path containing a sensitive component (.ssh, .gnupg, .env, *.pem, ...) is refused even inside
  an approved root, and no approval can override that;
- file descriptors are re-checked after opening (`/proc/self/fd`) to close the check/use race.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

# Matched case-insensitively against every component of the path below the approved root.
SENSITIVE_PATTERNS = (
    ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".netrc", ".pgpass", ".npmrc", ".pypirc",
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "*.keystore",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "credentials*", "secrets*",
    "*.token", "*.secret", "*.secrets", ".git-credentials", "shadow", "passwd",
)  # fmt: skip


class PathDenied(ValueError):
    """The path is outside the approved scope or sensitive. Never overridable by approval."""


@dataclass(frozen=True)
class Scoped:
    path: Path
    root: Path
    writable: bool


def is_sensitive(rel: Path) -> bool:
    return any(
        fnmatch.fnmatch(part.lower(), pat) for part in rel.parts for pat in SENSITIVE_PATTERNS
    )


def _check_raw(raw: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise PathDenied("invalid path")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise PathDenied("path must be absolute")
    return p


def resolve_scoped(
    raw: str,
    read_roots: list[Path],
    write_roots: list[Path],
    *,
    write: bool,
    must_exist: bool,
) -> Scoped:
    """Resolve `raw` and prove it lies inside an approved root (a writable root if `write`)."""
    p = _check_raw(raw)
    if must_exist:
        try:
            real = p.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            raise PathDenied("path does not exist") from None
        except (OSError, RuntimeError):
            raise PathDenied("path cannot be resolved") from None
    else:
        name = p.name
        if name in ("", ".", "..") or "/" in name:
            raise PathDenied("invalid file name")
        try:
            parent = p.parent.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            raise PathDenied("parent directory does not exist") from None
        except (OSError, RuntimeError):
            raise PathDenied("path cannot be resolved") from None
        real = parent / name
    roots = write_roots if write else read_roots
    for root in roots:
        if real == root or real.is_relative_to(root):
            if is_sensitive(real.relative_to(root)):
                raise PathDenied("path is protected (sensitive name)")
            return Scoped(real, root, writable=root in write_roots)
    raise PathDenied(
        "path is not in a writable approved folder" if write else "path is outside approved folders"
    )


def verify_fd_in_root(fd: int, root: Path) -> None:
    """After open(): confirm the descriptor really points inside `root` (symlink-swap defence)."""
    actual = Path(os.path.realpath(f"/proc/self/fd/{fd}"))
    if not (actual == root or actual.is_relative_to(root)) or is_sensitive(
        actual.relative_to(root)
    ):
        raise PathDenied("path changed while being opened")
