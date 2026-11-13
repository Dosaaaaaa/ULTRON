"""One process, two listeners: local http for the window, https for the phone, shared state."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
TOKEN = "serve-test-token-" + "x" * 20


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cert(tmp_path: Path) -> Path:
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=t",
         "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(tmp_path / "key.pem"),
         "-out", str(tmp_path / "cert.pem")],
        check=True, capture_output=True,
    )  # fmt: skip
    return tmp_path


def call(url: str, path: str, cert: Path | None = None, body=None, token=TOKEN, method=None):
    ctx = ssl.create_default_context(cafile=str(cert)) if cert else None
    if ctx:
        ctx.check_hostname = False
    req = urllib.request.Request(
        url + path,
        method=method or ("POST" if body is not None else "GET"),
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=4, context=ctx) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except OSError:
        return 0, None


@pytest.fixture
def stack(tmp_path):
    certs = make_cert(tmp_path)
    http, tls = free_port(), free_port()
    env = {
        **os.environ,
        "ULTRON_API_TOKEN": TOKEN,
        "ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 's.db'}",
        "ULTRON_MEMORY_ENABLED": "false",
        "ULTRON_ALLOW_CLOUD": "false",
    }
    env.pop("ULTRON_TLS_CERT_FILE", None)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "app.serve", "--http-port", str(http),
            "--tls-host", "127.0.0.1", "--tls-port", str(tls),
            "--tls-cert", str(certs / "cert.pem"), "--tls-key", str(certs / "key.pem"),
        ],
        cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    plain, secure = f"http://127.0.0.1:{http}", f"https://127.0.0.1:{tls}"
    for _ in range(100):
        if (
            call(plain, "/health")[0] == 200
            and call(secure, "/health", certs / "cert.pem")[0] == 200
        ):
            break
        time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("the two listeners did not come up")
    yield plain, secure, certs / "cert.pem", proc
    if proc.poll() is None:
        proc.kill()


def test_both_addresses_answer_and_share_one_application(stack):
    plain, secure, cert, _ = stack
    # a code made on the local address names the certificate served on the Wi-Fi address
    status, code = call(
        plain, "/v1/devices/pairing-code", body={"capabilities": ["chat", "notify"]}
    )
    assert status == 200 and code["tls_fingerprint_sha256"]
    # the phone pairs over https; the window then sees that phone over http: shared state
    status, paired = call(
        secure,
        "/v1/devices/pair",
        cert,
        {"code": code["code"], "name": "Pixel", "kind": "phone"},
        token="x",
    )
    assert status == 200 and paired["token"]
    status, devices = call(plain, "/v1/devices")
    assert status == 200 and [d["name"] for d in devices] == ["Pixel"]
    # and a notification sent from the local side reaches the phone's queue on the https side
    status, sent = call(plain, "/v1/notify", body={"title": "hi"})
    assert status == 200 and sent == {"devices": 1}


def test_the_https_side_refuses_a_client_that_does_not_trust_the_certificate(stack):
    _, secure, _, _ = stack
    assert call(secure, "/health")[0] == 0  # the system trust store does not know this certificate


def test_one_termination_signal_stops_both_listeners(stack):
    plain, secure, cert, proc = stack
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=15)
    assert call(plain, "/health")[0] == 0 and call(secure, "/health", cert)[0] == 0
