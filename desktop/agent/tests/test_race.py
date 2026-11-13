"""Check-then-use races: the policy engine proved a path was safe, then the filesystem changed.

These call the handlers directly with a context that still holds the ORIGINAL (safe) resolution,
as it would if an attacker swapped a symlink in between the check and the use.
"""

from __future__ import annotations

import pytest

from ultron_agent.paths import PathDenied
from ultron_agent.registry import ToolContext
from ultron_agent.tools import files


def ctx_for(path, root, raw):
    return ToolContext([root], [root], scoped={raw: path}, roots_of={raw: root}, trash_dir=None)


@pytest.fixture
def swapped(ws, short_tmp):
    """ws/target is a symlink to a file OUTSIDE the root; ctx claims it is a safe in-root path."""
    victim = short_tmp / "victim.txt"
    victim.write_text("original")
    (ws / "target").symlink_to(victim)
    root = ws.resolve()
    raw = str(ws / "target")
    return victim, ctx_for(root / "target", root, raw), raw


def test_read_does_not_follow_swapped_symlink(swapped):
    _victim, ctx, raw = swapped
    with pytest.raises(OSError):  # ELOOP from O_NOFOLLOW
        files.files_read(files.ReadArgs(path=raw), ctx)


def test_append_does_not_follow_swapped_symlink(swapped):
    victim, ctx, raw = swapped
    with pytest.raises(OSError):
        files.files_append(files.AppendArgs(path=raw, content="PWNED"), ctx)
    assert victim.read_text() == "original"


def test_create_does_not_follow_swapped_symlink(swapped):
    victim, ctx, raw = swapped
    with pytest.raises(OSError):
        files.files_create(files.CreateArgs(path=raw, content="PWNED"), ctx)
    assert victim.read_text() == "original"


def test_delete_does_not_trash_swapped_symlink_target(swapped, short_tmp):
    victim, ctx, raw = swapped
    ctx.trash_dir = short_tmp / "trash"
    with pytest.raises(OSError):
        files.files_delete(files.PathArgs(path=raw), ctx)
    assert victim.read_text() == "original"


def test_parent_directory_swapped_for_symlink_is_caught(ws, short_tmp):
    """ws/dir was a real directory when checked; before use it became a link to somewhere else."""
    elsewhere = short_tmp / "elsewhere"
    elsewhere.mkdir()
    (ws / "dir").symlink_to(elsewhere)
    root = ws.resolve()
    raw = str(ws / "dir" / "new.txt")
    ctx = ctx_for(root / "dir" / "new.txt", root, raw)
    with pytest.raises((PathDenied, OSError)):
        files.files_create(files.CreateArgs(path=raw, content="PWNED"), ctx)
    assert not (elsewhere / "new.txt").exists()


def test_handlers_refuse_paths_policy_did_not_clear(ws):
    ctx = ToolContext([ws], [ws])
    with pytest.raises(PathDenied):
        files.files_read(files.ReadArgs(path=str(ws / "x")), ctx)
