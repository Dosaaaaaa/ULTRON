"""The blue orb interface (a fork of the MIT ultron-by-sagar-builds UI) shown inside the window.

`desktop/orb-ui` is a Next.js + Three.js app, built once to static files (`npm run build` ->
`out/`). This module serves that folder from a loopback-only HTTP server on a random port and
shows it in a locked-down QWebEngineView: it can only ever load that one origin, never asks for
the camera or microphone, and gets the assistant's state pushed in through `set_state`.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

try:  # QtWebEngine ships with the PySide6 wheel, but keep the native orb as a fallback.
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    HAVE_WEBENGINE = True
except ImportError:  # pragma: no cover - exercised only on a broken install
    HAVE_WEBENGINE = False

ORB_DIR = Path(__file__).resolve().parents[2] / "orb-ui" / "out"
STATES = ("off", "starting", "standby", "muted", "listening", "thinking", "speaking")


def web_orb_available(orb_dir: Path = ORB_DIR) -> bool:
    return HAVE_WEBENGINE and (orb_dir / "index.html").is_file()


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # keep the terminal clean
        pass

    def do_POST(self) -> None:  # static files only
        self.send_error(405)

    do_PUT = do_DELETE = do_PATCH = do_POST


class OrbServer:
    """Serves `orb_dir` on 127.0.0.1 only, on an ephemeral port."""

    def __init__(self, orb_dir: Path = ORB_DIR) -> None:
        handler = functools.partial(_Quiet, directory=str(orb_dir))
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self.port: int = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


if HAVE_WEBENGINE:

    class _LockedPage(QWebEnginePage):
        """Only the orb's own origin may load; every permission request is refused."""

        def __init__(self, origin: str, parent=None) -> None:
            super().__init__(parent)
            self._origin = QUrl(origin)
            self.featurePermissionRequested.connect(self._deny)
            self.permissionRequested.connect(lambda permission: permission.deny())

        def _deny(self, url, feature) -> None:
            self.setFeaturePermission(url, feature, QWebEnginePage.PermissionDeniedByUser)

        def acceptNavigationRequest(self, url, nav_type, is_main_frame) -> bool:  # noqa: N802
            # Compare scheme, host and port as parts: a string prefix test would admit
            # "http://127.0.0.1:54321" for an origin on port 5432 and "http://127.0.0.1:5432@evil".
            return (
                url.scheme() == self._origin.scheme()
                and url.host() == self._origin.host()
                and url.port() == self._origin.port()
                and not url.userInfo()
            )


class WebOrbView(QWidget):
    """Drop-in for HologramView: same `set_state`, rendered by the web orb."""

    failed = Signal(str)  # the page could not start (no WebGL) or never became ready

    def __init__(self, parent: QWidget | None = None, *, orb_dir: Path = ORB_DIR) -> None:
        super().__init__(parent)
        self.state = "standby"
        self.ready = False
        self._tries = 0
        self.server = OrbServer(orb_dir)
        self.view = QWebEngineView(self)
        self.page = _LockedPage(self.server.origin, self.view)
        self.view.setPage(self.page)
        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, False)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.view.loadFinished.connect(self._on_loaded)
        self.view.setUrl(QUrl(f"{self.server.origin}/?embed=1"))

    def hideEvent(self, event) -> None:  # noqa: N802
        """A hidden window must not keep a WebGL page rendering: freeze it (about the only large
        CPU cost of running in the background)."""
        super().hideEvent(event)
        QTimer.singleShot(0, lambda: self._set_lifecycle(hidden=True))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._set_lifecycle(hidden=False)

    def _set_lifecycle(self, *, hidden: bool) -> None:
        if hidden != self.isVisible():  # the state may have changed again since it was queued
            state = QWebEnginePage.LifecycleState
            self.page.setLifecycleState(state.Frozen if hidden else state.Active)

    def set_state(self, state: str) -> None:
        self.state = state if state in STATES else "standby"
        if self.ready:
            self._push()

    # the page defines window.ultronSetState once React has mounted; poll briefly for it
    def _on_loaded(self, ok: bool) -> None:
        if ok:
            self._probe()
        else:
            self.failed.emit("the orb page failed to load")

    def _probe(self) -> None:
        self.page.runJavaScript(
            "window.ultronFailed ? 'failed' : typeof window.ultronSetState === 'function'",
            self._probed,
        )

    def _probed(self, present) -> None:
        if present == "failed":
            self.failed.emit("WebGL is not available in the embedded browser")
        elif present is True:
            self.ready = True
            self._push()
        elif self._tries < 40:
            self._tries += 1
            QTimer.singleShot(150, self._probe)
        else:
            self.failed.emit("the orb page did not start")

    def _push(self) -> None:
        self.page.runJavaScript(f"window.ultronSetState({self.state!r})")

    def set_level(self, level: float) -> None:
        """Speech loudness 0..1 from the real output audio; the core follows it while speaking."""
        if self.ready:
            level = min(1.0, max(0.0, float(level)))
            self.page.runJavaScript(f"window.ultronSetLevel && window.ultronSetLevel({level:.3f})")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        if self.server is not None:
            self.view.setUrl(QUrl("about:blank"))
            self.server.stop()
            self.server = None  # type: ignore[assignment]
