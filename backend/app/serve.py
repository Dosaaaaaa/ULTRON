"""Run ULTRON on two addresses from ONE process: plain http on this computer (the window and the
voice client use it) and, if a certificate is given, https on the Wi-Fi address for the phone.

One process matters: the push connections, the reminder scheduler and the pairing codes live in
memory, so two separate servers could not see each other's phones. Both listeners serve the same
application object; only the first runs the application's start-up and shutdown.

    python -m app.serve --http-port 8000 --tls-host 192.168.0.112 --tls-port 8443 \\
        --tls-cert ~/.config/ultron/tls/cert.pem --tls-key ~/.config/ultron/tls/key.pem
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

import uvicorn


class SecondListener(uvicorn.Server):
    """The https listener: signals and the application lifespan stay with the first listener."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def serve(
    http_host: str,
    http_port: int,
    tls_host: str | None = None,
    tls_port: int = 8443,
    tls_cert: Path | None = None,
    tls_key: Path | None = None,
    log_level: str = "warning",
) -> None:
    if (
        tls_cert is not None
    ):  # the app reports this certificate's fingerprint to the phone at pairing
        os.environ["ULTRON_TLS_CERT_FILE"] = str(tls_cert)
    from app.main import app_factory  # noqa: PLC0415 - after the environment is set

    app = app_factory()
    first = uvicorn.Server(
        uvicorn.Config(app, host=http_host, port=http_port, log_level=log_level, lifespan="on")
    )
    first_task = asyncio.create_task(first.serve())
    while not first.started and not first_task.done():  # noqa: ASYNC110 - uvicorn exposes only a flag
        await asyncio.sleep(0.05)
    second_task: asyncio.Task | None = None
    second: SecondListener | None = None
    if tls_cert and tls_key and tls_host and not first_task.done():
        second = SecondListener(
            uvicorn.Config(
                app,
                host=tls_host,
                port=tls_port,
                ssl_certfile=str(tls_cert),
                ssl_keyfile=str(tls_key),
                log_level=log_level,
                lifespan="off",
            )
        )
        second_task = asyncio.create_task(second.serve())
    try:
        await first_task
    finally:
        if second is not None and second_task is not None:
            second.should_exit = True
            await second_task


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.serve")
    ap.add_argument("--http-host", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--tls-host", help="the computer's Wi-Fi address for the phone (https)")
    ap.add_argument("--tls-port", type=int, default=8443)
    ap.add_argument("--tls-cert", type=Path)
    ap.add_argument("--tls-key", type=Path)
    ap.add_argument("--log-level", default="warning")
    a = ap.parse_args(argv)
    asyncio.run(
        serve(a.http_host, a.http_port, a.tls_host, a.tls_port, a.tls_cert, a.tls_key, a.log_level)
    )


if __name__ == "__main__":
    main()
