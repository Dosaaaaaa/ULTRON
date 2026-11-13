from __future__ import annotations

import os
from pathlib import Path

import pytest

from ultron_agent.paths import PathDenied, resolve_scoped, verify_fd_in_root


def r(raw, roots, writable=(), *, write=False, must_exist=True):
    return resolve_scoped(
        str(raw),
        [p.resolve() for p in roots],
        [p.resolve() for p in writable],
        write=write,
        must_exist=must_exist,
    )


def test_inside_root_ok(ws):
    (ws / "a.txt").write_text("x")
    s = r(ws / "a.txt", [ws])
    assert s.path == (ws / "a.txt").resolve() and s.root == ws.resolve()


@pytest.mark.parametrize("bad", ["relative/path", "", "~root/../x", "a\x00b"])
def test_malformed_denied(ws, bad):
    with pytest.raises(PathDenied):
        r(bad, [ws])


def test_dotdot_escape_denied(ws, short_tmp):
    (short_tmp / "secret.txt").write_text("s")
    with pytest.raises(PathDenied, match="outside"):
        r(ws / ".." / "secret.txt", [ws])


def test_symlink_escape_denied(ws, short_tmp):
    outside = short_tmp / "outside.txt"
    outside.write_text("s")
    (ws / "link").symlink_to(outside)
    with pytest.raises(PathDenied, match="outside"):
        r(ws / "link", [ws])


def test_symlinked_directory_escape_denied(ws, short_tmp):
    d = short_tmp / "elsewhere"
    d.mkdir()
    (d / "f.txt").write_text("s")
    (ws / "d").symlink_to(d)
    with pytest.raises(PathDenied):
        r(ws / "d" / "f.txt", [ws])


@pytest.mark.parametrize(
    "name", [".env", ".env.local", "id_rsa", "server.pem", "my.key", "credentials.json", ".ssh"]
)
def test_sensitive_names_denied_even_inside_root(ws, name):
    p = ws / name
    p.write_text("x") if name != ".ssh" else p.mkdir()
    with pytest.raises(PathDenied, match="protected"):
        r(p, [ws])


def test_sensitive_directory_component_denied(ws):
    (ws / ".ssh").mkdir()
    (ws / ".ssh" / "config").write_text("x")
    with pytest.raises(PathDenied, match="protected"):
        r(ws / ".ssh" / "config", [ws])


def test_root_name_itself_may_look_sensitive(short_tmp):
    """Only components BELOW the approved root are screened (the user chose the root)."""
    root = short_tmp / "keys"
    root.mkdir()
    (root / "n.txt").write_text("x")
    assert r(root / "n.txt", [root]).path.name == "n.txt"


def test_write_requires_writable_root(ws, ro):
    (ro / "f.txt").write_text("x")
    assert r(ro / "f.txt", [ws, ro], [ws]).writable is False
    with pytest.raises(PathDenied, match="writable"):
        r(ro / "f.txt", [ws, ro], [ws], write=True)


def test_new_file_needs_existing_parent_and_valid_name(ws):
    assert r(ws / "new.txt", [ws], [ws], write=True, must_exist=False).path.name == "new.txt"
    with pytest.raises(PathDenied, match="parent"):
        r(ws / "nope" / "new.txt", [ws], [ws], write=True, must_exist=False)
    with pytest.raises(PathDenied):
        r(str(ws) + "/..", [ws], [ws], write=True, must_exist=False)


def test_new_file_in_symlinked_parent_outside_denied(ws, short_tmp):
    out = short_tmp / "out"
    out.mkdir()
    (ws / "lnk").symlink_to(out)
    with pytest.raises(PathDenied):
        r(ws / "lnk" / "x.txt", [ws], [ws], write=True, must_exist=False)


def test_verify_fd_detects_swapped_symlink(ws, short_tmp):
    out = short_tmp / "out"
    out.mkdir()
    (out / "f").write_text("x")
    fd = os.open(out / "f", os.O_RDONLY)
    try:
        with pytest.raises(PathDenied, match="changed"):
            verify_fd_in_root(fd, ws.resolve())
    finally:
        os.close(fd)


def test_roots_must_not_be_broad(cfg):
    for bad in ["/", "/etc", str(Path.home()), "/usr", "relative"]:
        with pytest.raises(Exception):  # noqa: B017,PT011
            cfg.add_root(bad)
