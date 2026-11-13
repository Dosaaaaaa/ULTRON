"""The `ultron` command: real server, real TLS, real files."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "cli-test-token-" + "x" * 20


def load_cli():
    path = ROOT / "bin" / "ultron"
    loader = importlib.machinery.SourceFileLoader("ultron_cli", str(path))
    spec = importlib.util.spec_from_loader("ultron_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cli = load_cli()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(tmp_path, *, tls: Path | None = None):
    port = free_port()
    env = {
        **os.environ,
        "ULTRON_API_TOKEN": TOKEN,
        "ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}",
        "ULTRON_MEMORY_ENABLED": "false",
        "ULTRON_ALLOW_CLOUD": "false",
        "ULTRON_SCHEDULER_ENABLED": "false",
    }
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app_factory", "--factory",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]  # fmt: skip
    if tls:
        env["ULTRON_TLS_CERT_FILE"] = str(tls / "cert.pem")
        cmd += ["--ssl-certfile", str(tls / "cert.pem"), "--ssl-keyfile", str(tls / "key.pem")]
    proc = subprocess.Popen(
        cmd, cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    scheme = "https" if tls else "http"
    url = f"{scheme}://127.0.0.1:{port}"
    cli_env = {"ULTRON_API_TOKEN": TOKEN, "ULTRON_BACKEND_URL": url}
    if tls:
        cli_env["ULTRON_TLS_CERT_FILE"] = str(tls / "cert.pem")
    for _ in range(80):
        if cli.call("GET", url, "/health", cli_env, timeout=1)[0] == 200:
            return proc, url, cli_env
        time.sleep(0.25)
    proc.kill()
    raise RuntimeError("test server did not start")


def make_cert(tmp_path) -> Path:
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=t",
         "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(tmp_path / "key.pem"),
         "-out", str(tmp_path / "cert.pem")],
        check=True, capture_output=True,
    )  # fmt: skip
    return tmp_path


def test_env_files_are_parsed_without_surprises(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nULTRON_API_TOKEN='abc def'\nEMPTY=\n  SPACED = value \nnot a line\n")
    assert cli.read_env(f) == {"ULTRON_API_TOKEN": "abc def", "EMPTY": "", "SPACED": "value"}
    assert cli.read_env(tmp_path / "missing") == {}


def test_doctor_reports_without_ever_printing_the_token(capsys, monkeypatch):
    monkeypatch.setenv("ULTRON_API_TOKEN", TOKEN)
    monkeypatch.setenv("ULTRON_OLLAMA_BASE_URL", "http://127.0.0.1:9")  # nothing there
    cli.cmd_doctor(None, cli.read_env())
    out = capsys.readouterr().out
    assert TOKEN not in out and "[FAIL] ollama" in out and "api token" in out


def test_pair_makes_a_code_against_a_real_server_and_warns_when_there_is_no_tls(tmp_path, capsys):
    proc, url, env = start_server(tmp_path)
    try:
        args = type(
            "A", (), {"url": url, "phone_url": "https://192.168.0.5:8443", "powers": "chat,notify"}
        )
        assert cli.cmd_pair(args, env) == 0
        out = capsys.readouterr().out
        assert "Pairing code" in out and "WARNING" in out and "not served over TLS" in out
        assert TOKEN not in out
    finally:
        proc.kill()


def test_pair_over_tls_pins_the_certificate_and_prints_its_fingerprint(tmp_path, capsys):
    certs = make_cert(tmp_path)
    proc, url, env = start_server(tmp_path, tls=certs)
    try:
        args = type(
            "A",
            (),
            {"url": url, "phone_url": "https://192.168.0.5:8443", "powers": "chat,notify,call"},
        )
        assert cli.cmd_pair(args, env) == 0
        out = capsys.readouterr().out
        shown = subprocess.run(
            [
                "openssl",
                "x509",
                "-in",
                str(certs / "cert.pem"),
                "-noout",
                "-fingerprint",
                "-sha256",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        want = shown.split("=", 1)[1].strip()
        assert f"Fingerprint   : {want}" in out and "Pairing code" in out
        # a client that does not hold that certificate cannot talk to the server at all
        assert cli.call("GET", url, "/health", {}, timeout=2)[0] == 0
        # status sees the server and counts zero phones
        assert cli.cmd_status(None, env) == 0
        assert "0 paired" in capsys.readouterr().out
    finally:
        proc.kill()


def test_a_wrong_token_or_no_server_is_reported_not_crashed(tmp_path, capsys):
    args = type(
        "A", (), {"url": f"http://127.0.0.1:{free_port()}", "phone_url": None, "powers": "chat"}
    )
    assert cli.cmd_pair(args, {"ULTRON_API_TOKEN": TOKEN}) == 1
    assert "could not get a pairing code" in capsys.readouterr().err
    assert cli.cmd_pair(args, {}) == 1  # no token configured


def test_backup_makes_a_consistent_private_copy(tmp_path, capsys, monkeypatch):
    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute("create table t (x text)")
    con.execute("insert into t values ('kept')")
    con.commit()
    monkeypatch.setattr(cli, "BACKEND", tmp_path)
    assert cli.cmd_backup(None, {"ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{db}"}) == 0
    (copy,) = list((tmp_path / "data/backups").glob("ultron-*.db"))
    assert sqlite3.connect(copy).execute("select x from t").fetchall() == [("kept",)]
    assert oct(copy.stat().st_mode)[-3:] == "600"
    con.close()
    assert (
        cli.cmd_backup(None, {"ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'none.db'}"})
        == 1
    )


def test_the_service_command_changes_nothing_without_yes(tmp_path, capsys, monkeypatch):
    unit = tmp_path / "ultron-backend.service"
    monkeypatch.setattr(cli, "UNIT", unit)
    args = type("A", (), {"action": "install", "yes": False})
    assert cli.cmd_service(args, {}) == 0
    assert not unit.exists() and "--yes" in capsys.readouterr().out


def test_stop_ignores_a_stale_pid_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PIDFILE", tmp_path / "pid")
    (tmp_path / "pid").write_text("999999")  # no such process
    assert cli.cmd_stop(None, {}) == 0
    assert "no backend started" in capsys.readouterr().out


def test_the_launcher_writes_a_valid_menu_entry_and_icon_only_under_the_data_home(
    tmp_path, monkeypatch, capsys
):
    data = tmp_path / "share"
    monkeypatch.setattr(cli, "DATA", data)
    monkeypatch.setattr(cli, "DESKTOP_FILE", data / "applications/ultron.desktop")
    monkeypatch.setattr(cli, "ICON_FILE", data / "icons/hicolor/scalable/apps/ultron.svg")
    args = type("A", (), {"action": "install"})
    assert cli.cmd_launcher(args, {}) == 0
    entry = (data / "applications/ultron.desktop").read_text()
    assert "Categories=Utility;\n" in entry  # one main category: it must not appear twice in menus
    assert (
        f'Exec="{ROOT}/bin/ultron" start' in entry
        and "Terminal=false" in entry
        and "Icon=ultron" in entry
    )
    assert (data / "icons/hicolor/scalable/apps/ultron.svg").read_text().startswith("<?xml")
    made = sorted(p.name for p in data.rglob("*") if p.is_file() and p.suffix != ".cache")
    assert made == ["ultron.desktop", "ultron.svg"]  # (icon/desktop caches come from system tools)
    if shutil.which("desktop-file-validate"):
        r = subprocess.run(
            ["desktop-file-validate", str(data / "applications/ultron.desktop")],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
    args.action = "remove"
    assert cli.cmd_launcher(args, {}) == 0
    assert not list(data.rglob("*.desktop")) and not list(data.rglob("*.svg"))


def test_a_path_with_spaces_or_quotes_is_quoted_in_the_menu_entry():
    entry = cli.desktop_entry(Path('/home/a b/My "ULTRON"'))
    assert 'Exec="/home/a b/My \\"ULTRON\\"/bin/ultron" start' in entry


def test_starting_takes_one_backup_a_day_before_the_backend_runs(tmp_path, monkeypatch, capsys):
    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute("create table t (x text)")
    con.execute("insert into t values ('before')")
    con.commit()
    con.close()
    monkeypatch.setattr(cli, "BACKEND", tmp_path)
    env = {"ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    first = cli.auto_backup(env)
    assert first is not None and sqlite3.connect(first).execute("select x from t").fetchall() == [
        ("before",)
    ]
    assert cli.auto_backup(env) is None  # already backed up today
    assert (
        cli.auto_backup({"ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'nope.db'}"})
        is None
    )
    old = tmp_path / "data/backups"
    for i in range(20):
        (old / f"ultron-2020010{i % 10}-{i:06d}.db").write_bytes(b"x")
    (old / first.name).rename(
        old / "ultron-19990101-000000.db"
    )  # pretend yesterday's backup is older
    cli.auto_backup(env)
    assert len(list(old.glob("ultron-*.db"))) <= 15


def test_phone_setup_makes_a_private_certificate_once_and_prints_the_firewall_line(
    tmp_path, monkeypatch, capsys
):
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    monkeypatch.setattr(cli, "TLS_DIR", tmp_path / "tls")
    monkeypatch.setattr(cli, "lan_ip", lambda: "192.168.7.20")
    args = type("A", (), {"action": "setup", "renew": False})
    assert cli.cmd_phone(args, {}) == 0
    out = capsys.readouterr().out
    cert, key = tmp_path / "tls/cert.pem", tmp_path / "tls/key.pem"
    assert "Certificate made" in out and "Fingerprint:" in out
    assert "sudo ufw allow from 192.168.7.0/24 to any port 8443 proto tcp" in out
    assert (
        oct(key.stat().st_mode)[-3:] == "600"
        and oct((tmp_path / "tls").stat().st_mode)[-3:] == "700"
    )
    san = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"],
                         capture_output=True, text=True).stdout  # fmt: skip
    assert "192.168.7.20" in san and "127.0.0.1" in san
    first = cert.read_bytes()
    assert cli.cmd_phone(args, {}) == 0 and "already exists" in capsys.readouterr().out
    assert (
        cert.read_bytes() == first
    )  # not silently regenerated: the phone would have to pair again


def test_phone_setup_refuses_without_a_wifi_address(monkeypatch, capsys):
    monkeypatch.setattr(cli, "lan_ip", lambda: None)
    assert cli.cmd_phone(type("A", (), {"action": "setup", "renew": False}), {}) == 1
    assert "Wi-Fi address" in capsys.readouterr().err


def test_start_runs_one_process_on_two_addresses_and_the_phone_listener_follows_the_network(
    tmp_path,
):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cmd, shown = cli.backend_command("127.0.0.1", 8000, (cert, key), "192.168.7.20")
    assert cmd[1:3] == ["-m", "app.serve"] and shown == "192.168.7.20"
    assert str(cert) in cmd and "uvicorn" not in " ".join(cmd)
    # Regression: the https listener was bound to the Wi-Fi address seen at start-up. After the
    # laptop changed networks the QR pointed at the new address, the server at the old one.
    assert cmd[cmd.index("--tls-host") + 1] == "0.0.0.0"
    cmd, shown = cli.backend_command(
        "127.0.0.1", 8000, (cert, key), None
    )  # no Wi-Fi yet: still listens
    assert cmd[cmd.index("--tls-host") + 1] == "0.0.0.0" and shown == "all interfaces"
    plain, none = cli.backend_command("127.0.0.1", 8000, None, "192.168.7.20")
    assert plain[0].endswith("uvicorn") and none is None  # no certificate: plain local server


def test_doctor_points_at_phone_setup_when_there_is_no_certificate(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "TLS_DIR", tmp_path / "none")
    monkeypatch.setenv("ULTRON_API_TOKEN", TOKEN)
    monkeypatch.delenv("ULTRON_TLS_CERT_FILE", raising=False)
    cli.cmd_doctor(None, {})
    assert "ultron phone setup" in capsys.readouterr().out
