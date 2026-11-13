"""File tools. Paths arrive here already proven to be inside approved roots by the policy engine;
this module still opens everything through directory file descriptors with O_NOFOLLOW and
re-verifies the descriptor, so a symlink swapped in after the check cannot redirect an operation.

Deliberately NOT implemented: overwriting an existing file, deleting directories, permanent
deletion, changing permissions, executing files. Deletion goes to the user's Trash.
"""

from __future__ import annotations

import contextlib
import errno
import fnmatch
import hashlib
import os
import shutil
import stat
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ultron_agent.levels import Level
from ultron_agent.paths import PathDenied, is_sensitive, verify_fd_in_root
from ultron_agent.registry import PathUse, ToolContext, ToolSpec

MAX_READ = 65536
MAX_CREATE = 1_048_576
MAX_APPEND = 262_144
MAX_COPY = 50 * 1024 * 1024
_CHUNK = 1 << 20


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathArgs(_Strict):
    path: str = Field(min_length=1, max_length=4096)


class ListArgs(PathArgs):
    include_hidden: bool = False
    limit: int = Field(default=200, ge=1, le=1000)


class SearchArgs(_Strict):
    path: str = Field(min_length=1, max_length=4096)
    pattern: str = Field(min_length=1, max_length=120)  # substring, or glob if it has * ? [
    kind: str = Field(default="any", pattern="^(any|file|dir)$")
    max_results: int = Field(default=50, ge=1, le=200)
    max_depth: int = Field(default=6, ge=0, le=12)
    include_hidden: bool = False


class ReadArgs(PathArgs):
    max_bytes: int = Field(default=32768, ge=1, le=MAX_READ)


class CreateArgs(PathArgs):
    content: str = Field(max_length=MAX_CREATE)


class AppendArgs(PathArgs):
    content: str = Field(min_length=1, max_length=MAX_APPEND)


class TwoPathArgs(_Strict):
    src: str = Field(min_length=1, max_length=4096)
    dst: str = Field(min_length=1, max_length=4096)


# ------------------------------------------------------------------ descriptor helpers
def _open_dir(path: Path, root: Path) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        verify_fd_in_root(fd, root)
    except PathDenied:
        os.close(fd)
        raise
    return fd


def _open_file(
    path: Path, root: Path, flags: int, mode: int = 0o600, *, must_be_regular: bool = True
) -> int:
    """open() relative to a verified parent directory descriptor."""
    pfd = _open_dir(path.parent, root)
    try:
        fd = os.open(
            path.name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, mode, dir_fd=pfd
        )
    finally:
        os.close(pfd)
    try:
        verify_fd_in_root(fd, root)
        if must_be_regular and not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PathDenied("not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _scoped(ctx: ToolContext, raw: str) -> tuple[Path, Path]:
    try:
        return ctx.scoped[raw], ctx.roots_of[raw]
    except KeyError:  # a handler must never see a path the policy engine did not clear
        raise PathDenied("path was not cleared by policy") from None


def _hidden(name: str) -> bool:
    return name.startswith(".")


# ------------------------------------------------------------------ read-only tools
def files_list(a: ListArgs, ctx: ToolContext) -> dict[str, Any]:
    path, root = _scoped(ctx, a.path)
    fd = _open_dir(path, root)
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(fd) as it:
            for e in sorted(it, key=lambda e: e.name.lower()):
                if (not a.include_hidden and _hidden(e.name)) or is_sensitive(Path(e.name)):
                    continue
                if len(entries) >= a.limit:
                    truncated = True
                    break
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                kind = (
                    "dir"
                    if stat.S_ISDIR(st.st_mode)
                    else "link"
                    if stat.S_ISLNK(st.st_mode)
                    else "file"
                )
                entries.append(
                    {
                        "name": e.name,
                        "type": kind,
                        "size": st.st_size if kind == "file" else None,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
    finally:
        os.close(fd)
    return {"path": str(path), "entries": entries, "truncated": truncated}


def files_search(a: SearchArgs, ctx: ToolContext) -> dict[str, Any]:
    start, root = _scoped(ctx, a.path)
    pat = a.pattern.lower()
    is_glob = any(c in pat for c in "*?[")
    results: list[dict[str, Any]] = []
    scanned = 0
    timed_out = False

    def matches(name: str) -> bool:
        n = name.lower()
        return fnmatch.fnmatchcase(n, pat) if is_glob else pat in n

    stack: list[tuple[Path, int]] = [(start, 0)]
    while stack and len(results) < a.max_results:
        directory, depth = stack.pop()
        if time.monotonic() > ctx.deadline:
            timed_out = True
            break
        try:
            fd = _open_dir(directory, root)
        except (OSError, PathDenied):
            continue
        try:
            with os.scandir(fd) as it:
                children = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            children = []
        finally:
            os.close(fd)
        subdirs = []
        for e in children:
            scanned += 1
            if (not a.include_hidden and _hidden(e.name)) or is_sensitive(Path(e.name)):
                continue
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                is_file = e.is_file(follow_symlinks=False)
            except OSError:
                continue
            want = a.kind == "any" or (a.kind == "dir" and is_dir) or (a.kind == "file" and is_file)
            if want and (is_dir or is_file) and matches(e.name):
                results.append(
                    {"path": str(directory / e.name), "type": "dir" if is_dir else "file"}
                )
                if len(results) >= a.max_results:
                    break
            if is_dir and depth < a.max_depth:
                subdirs.append((directory / e.name, depth + 1))
        stack.extend(reversed(subdirs))
    return {
        "results": results,
        "scanned": scanned,
        "truncated": len(results) >= a.max_results,
        "timed_out": timed_out,
    }


def files_read(a: ReadArgs, ctx: ToolContext) -> dict[str, Any]:
    path, root = _scoped(ctx, a.path)
    fd = _open_file(path, root, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        data = fh.read(a.max_bytes + 1)
    truncated = len(data) > a.max_bytes
    data = data[: a.max_bytes]
    if b"\x00" in data[:8192]:
        raise ValueError("binary_file")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if truncated:  # we may have cut a multi-byte character in half
            text = data[:-3].decode("utf-8", errors="ignore")
        else:
            raise ValueError("not_utf8") from None
    return {"path": str(path), "size_bytes": size, "truncated": truncated, "text": text}


# ------------------------------------------------------------------ P2 tools (need approval)
def files_create(a: CreateArgs, ctx: ToolContext) -> dict[str, Any]:
    path, root = _scoped(ctx, a.path)
    data = a.content.encode()
    fd = _open_file(path, root, os.O_WRONLY | os.O_CREAT | os.O_EXCL)  # fails if it exists
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return {"created": str(path), "bytes": len(data)}


def files_append(a: AppendArgs, ctx: ToolContext) -> dict[str, Any]:
    path, root = _scoped(ctx, a.path)
    data = a.content.encode()
    fd = _open_file(path, root, os.O_WRONLY | os.O_APPEND)
    with os.fdopen(fd, "ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return {"appended_to": str(path), "bytes": len(data)}


def _copy_exclusive(src: Path, src_root: Path, dst: Path, dst_root: Path) -> int:
    sfd = _open_file(src, src_root, os.O_RDONLY)
    try:
        if os.fstat(sfd).st_size > MAX_COPY:
            raise ValueError("file_too_large")
        dfd = _open_file(dst, dst_root, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except BaseException:
        os.close(sfd)
        raise
    total = 0
    try:
        with os.fdopen(sfd, "rb") as s, os.fdopen(dfd, "wb") as d:
            while chunk := s.read(_CHUNK):
                total += len(chunk)
                if total > MAX_COPY:
                    raise ValueError("file_too_large")
                d.write(chunk)
            d.flush()
            os.fsync(d.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(dst)
        raise
    return total


def files_copy(a: TwoPathArgs, ctx: ToolContext) -> dict[str, Any]:
    src, src_root = _scoped(ctx, a.src)
    dst, dst_root = _scoped(ctx, a.dst)
    n = _copy_exclusive(src, src_root, dst, dst_root)
    return {"copied": str(src), "to": str(dst), "bytes": n}


def files_move(a: TwoPathArgs, ctx: ToolContext) -> dict[str, Any]:
    src, src_root = _scoped(ctx, a.src)
    dst, dst_root = _scoped(ctx, a.dst)
    sfd = _open_file(src, src_root, os.O_RDONLY)  # proves it is a regular file inside the root
    os.close(sfd)
    spfd, dpfd = _open_dir(src.parent, src_root), _open_dir(dst.parent, dst_root)
    try:
        try:
            # link() is atomic and fails if the destination exists, so nothing is overwritten
            os.link(src.name, dst.name, src_dir_fd=spfd, dst_dir_fd=dpfd, follow_symlinks=False)
        except OSError as e:
            if e.errno == errno.EEXIST:
                raise FileExistsError("destination_exists") from None
            if e.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK, errno.ENOTSUP):
                raise
            _copy_exclusive(src, src_root, dst, dst_root)  # other filesystem: copy, then remove
        os.unlink(src.name, dir_fd=spfd)
    finally:
        os.close(spfd)
        os.close(dpfd)
    return {"moved": str(src), "to": str(dst)}


# ------------------------------------------------------------------ P3: delete = move to Trash
def _trash_name(trash_files: Path, name: str) -> str:
    candidate, i = name, 1
    while (trash_files / candidate).exists() or (
        trash_files.parent / "info" / f"{candidate}.trashinfo"
    ).exists():
        i += 1
        candidate = f"{name}.{i}"
    return candidate


def _copy_exclusive_to(src: Path, src_root: Path, dst: Path) -> None:
    sfd = _open_file(src, src_root, os.O_RDONLY)
    with os.fdopen(sfd, "rb") as s_, open(dst, "xb") as d_:  # noqa: PTH123
        shutil.copyfileobj(s_, d_, _CHUNK)


def files_delete(a: PathArgs, ctx: ToolContext) -> dict[str, Any]:
    path, root = _scoped(ctx, a.path)
    if ctx.trash_dir is None:
        raise RuntimeError("trash_unavailable")
    fd = _open_file(path, root, os.O_RDONLY)  # regular file inside a writable root only
    os.close(fd)
    files_dir, info_dir = ctx.trash_dir / "files", ctx.trash_dir / "info"
    for d in (files_dir, info_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = _trash_name(files_dir, path.name)
    info = info_dir / f"{name}.trashinfo"
    info.write_text(
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(str(path))}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    spfd = _open_dir(path.parent, root)
    try:
        try:
            os.rename(path.name, files_dir / name, src_dir_fd=spfd)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            _copy_exclusive_to(path, root, files_dir / name)  # trash on another filesystem
            os.unlink(path.name, dir_fd=spfd)
    except BaseException:
        info.unlink(missing_ok=True)
        raise
    finally:
        os.close(spfd)
    return {"trashed": str(path), "trash_path": str(files_dir / name), "recoverable": True}


# ------------------------------------------------------------------ specs
def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _payload_audit(a: BaseModel) -> dict[str, Any]:
    d = a.model_dump()
    content = d.pop("content", None)
    if content is not None:
        d["content_bytes"] = len(content.encode())
        d["content_sha256"] = _sha(content)
    return d


SPECS = [
    ToolSpec(
        name="files.list",
        description="List a directory in an approved folder (hidden/protected names omitted).",
        input_model=ListArgs,
        level=Level.P0,
        timeout_s=5,
        handler=files_list,
        describe=lambda a: f"List {a.path}",
        paths=lambda a: [PathUse(a.path, write=False)],
    ),
    ToolSpec(
        name="files.search",
        description="Find files or folders by name (substring or glob) under an approved folder.",
        input_model=SearchArgs,
        level=Level.P0,
        timeout_s=10,
        handler=files_search,
        describe=lambda a: f"Search '{a.pattern}' under {a.path}",
        paths=lambda a: [PathUse(a.path, write=False)],
    ),
    ToolSpec(
        name="files.read",
        description="Read a text file (up to 64 KiB) inside an approved folder.",
        input_model=ReadArgs,
        level=Level.P0,
        timeout_s=5,
        handler=files_read,
        describe=lambda a: f"Read {a.path}",
        paths=lambda a: [PathUse(a.path, write=False)],
    ),
    ToolSpec(
        name="files.create",
        description="Create a NEW text file in a writable approved folder. Never overwrites.",
        input_model=CreateArgs,
        level=Level.P2,
        timeout_s=10,
        handler=files_create,
        describe=lambda a: f"Create {a.path} ({len(a.content.encode())} bytes)",
        paths=lambda a: [PathUse(a.path, write=True, must_exist=False)],
        audit_args=_payload_audit,
    ),
    ToolSpec(
        name="files.append",
        description="Append text to an existing file in a writable approved folder.",
        input_model=AppendArgs,
        level=Level.P2,
        timeout_s=10,
        handler=files_append,
        describe=lambda a: f"Append {len(a.content.encode())} bytes to {a.path}",
        paths=lambda a: [PathUse(a.path, write=True)],
        audit_args=_payload_audit,
    ),
    ToolSpec(
        name="files.copy",
        description="Copy a file (max 50 MiB) to a NEW path in a writable approved folder.",
        input_model=TwoPathArgs,
        level=Level.P2,
        timeout_s=30,
        handler=files_copy,
        describe=lambda a: f"Copy {a.src} to {a.dst}",
        paths=lambda a: [PathUse(a.src, write=False), PathUse(a.dst, write=True, must_exist=False)],
    ),
    ToolSpec(
        name="files.move",
        description="Move/rename a file to a NEW path; both must be in writable approved folders.",
        input_model=TwoPathArgs,
        level=Level.P2,
        timeout_s=30,
        handler=files_move,
        describe=lambda a: f"Move {a.src} to {a.dst}",
        paths=lambda a: [PathUse(a.src, write=True), PathUse(a.dst, write=True, must_exist=False)],
    ),
    ToolSpec(
        name="files.delete",
        description="Move one file to the Trash (recoverable). Directories are not supported.",
        input_model=PathArgs,
        level=Level.P3,
        timeout_s=10,
        handler=files_delete,
        describe=lambda a: f"Move {a.path} to the Trash",
        paths=lambda a: [PathUse(a.path, write=True)],
        confirm_text=lambda a: f"delete {Path(a.path).name}",
    ),
]
