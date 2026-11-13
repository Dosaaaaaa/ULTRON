"""The blue web orb: a loopback-only static server, a view locked to it, and the state bridge.

These run the real built page (desktop/orb-ui/out) in QtWebEngine with software WebGL. They are
skipped if the page has not been built (`npm run build` in desktop/orb-ui).
"""

from __future__ import annotations

import http.client
import os
import time

import pytest
from PySide6.QtCore import QUrl

os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu-compositing --use-gl=angle --use-angle=swiftshader-webgl "
    "--enable-unsafe-swiftshader --in-process-gpu",
)

from tests.conftest import wait_until  # noqa: E402
from ultron_desktop.hologram import HologramView  # noqa: E402
from ultron_desktop.main_window import MainWindow  # noqa: E402
from ultron_desktop.web_orb import ORB_DIR, OrbServer, WebOrbView, web_orb_available  # noqa: E402

pytestmark = pytest.mark.skipif(not web_orb_available(), reason="orb-ui/out is not built")


def get(server: OrbServer, path: str, method: str = "GET") -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request(method, path)
    return conn.getresponse()


# ------------------------------------------------------------------ server
def test_server_is_loopback_only_and_serves_the_page():
    server = OrbServer()
    try:
        assert server._httpd.server_address[0] == "127.0.0.1"
        r = get(server, "/")
        assert r.status == 200 and b"<html" in r.read().lower()
        assert get(server, "/nope.js").status == 404
    finally:
        server.stop()


def test_server_is_read_only_and_does_not_escape_its_folder():
    server = OrbServer()
    try:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            assert get(server, "/", method).status == 405, method
        for path in ("/../../../../etc/passwd", "/%2e%2e/%2e%2e/%2e%2e/etc/passwd"):
            r = get(server, path)
            assert b"root:" not in r.read(), path
    finally:
        server.stop()


# ------------------------------------------------------------------ the view
@pytest.fixture
def orb(qapp):
    v = WebOrbView()
    v.resize(900, 600)
    v.show()
    yield v
    v.shutdown()
    v.deleteLater()


def run_js(view: WebOrbView, expr: str):
    """Evaluate `expr` in the page and wait for the (asynchronous) result."""
    box: list = []
    view.page.runJavaScript(expr, box.append)
    wait_until(lambda: bool(box), what="javascript result")
    return box[0]


def page_text(view: WebOrbView, selector: str) -> str:
    return run_js(view, f"(document.querySelector({selector!r}) || {{}}).textContent || ''")


def test_state_reaches_the_real_page(orb):
    wait_until(lambda: orb.ready, timeout=20, what="orb page ready")
    for state, label in (("speaking", "SPEAKING"), ("muted", "MUTED"), ("listening", "LISTENING")):
        orb.set_state(state)

        def shown(label=label) -> bool:
            return page_text(orb, "[data-testid=state]") == label

        wait_until(shown, timeout=8, what=f"label {label}")


def test_unknown_state_falls_back_to_standby(orb):
    wait_until(lambda: orb.ready, timeout=20, what="orb page ready")
    orb.set_state("<script>alert(1)</script>")
    assert orb.state == "standby"


def test_state_set_before_the_page_is_ready_is_applied_afterwards(qapp):
    v = WebOrbView()
    try:
        v.set_state("thinking")  # page is still loading
        assert not v.ready
        wait_until(lambda: v.ready, timeout=20, what="orb page ready")
        wait_until(
            lambda: page_text(v, "[data-testid=state]") == "THINKING",
            timeout=8,
            what="queued state applied",
        )
    finally:
        v.shutdown()


def test_page_may_only_navigate_within_its_own_origin(orb):
    page = orb.page
    assert page.acceptNavigationRequest(QUrl(orb.server.origin + "/x"), None, True)
    for url in ("https://example.com/", "http://127.0.0.1:1/", "file:///etc/passwd"):
        assert not page.acceptNavigationRequest(QUrl(url), None, True), url


def test_a_look_alike_address_cannot_pass_the_origin_check(orb):
    # Review finding: a string prefix test let "...:5432" admit ":54321" and "host@evil" forms.
    origin = orb.server.origin  # http://127.0.0.1:<port>
    lookalikes = (
        origin + "1/",  # another port that starts with the same digits
        origin + "@evil.example/",  # the part after the @ is the host
        origin.replace("http://", "https://") + "/",  # wrong scheme
        origin.replace("127.0.0.1", "localhost") + "/",  # different host name
        origin + ".evil.example/",
    )
    for url in lookalikes:
        assert not orb.page.acceptNavigationRequest(QUrl(url), None, True), url
    assert orb.page.acceptNavigationRequest(QUrl(origin), None, True)  # the bare origin
    assert orb.page.acceptNavigationRequest(QUrl(origin + "/?embed=1#x"), None, True)


def test_page_cannot_get_the_camera_or_microphone(qapp, orb):
    wait_until(lambda: orb.ready, timeout=20, what="orb page ready")
    orb.page.runJavaScript(
        "navigator.mediaDevices.getUserMedia({audio: true, video: true})"
        ".then(() => { window.__media = 'GRANTED'; })"
        ".catch((e) => { window.__media = e.name; }); 0"
    )

    def settled() -> bool:
        return run_js(orb, "window.__media || 'pending'") != "pending"

    wait_until(settled, timeout=10, what="getUserMedia to settle")
    result = run_js(orb, "window.__media")
    assert result in {"NotAllowedError", "AbortError", "NotFoundError"}, result


def test_the_render_is_blue_not_amber(qapp, orb):
    wait_until(lambda: orb.ready, timeout=20, what="orb page ready")
    end = time.monotonic() + 3  # let a few frames render (events must keep flowing)
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    img = orb.grab().toImage()
    r = g = b = n = 0
    for y in range(0, img.height(), 6):
        for x in range(0, img.width(), 6):
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() > 90:  # only the lit pixels
                r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
    assert n > 200, "nothing lit: the orb did not render"
    assert b > r * 1.6 and b > g, (r / n, g / n, b / n)  # blue-dominant, no amber


# ------------------------------------------------------------------ failure and fallback
def test_a_page_that_reports_no_webgl_emits_failed(qapp, tmp_path):
    (tmp_path / "index.html").write_text("<html><script>window.ultronFailed = true</script></html>")
    v = WebOrbView(orb_dir=tmp_path)
    seen: list[str] = []
    v.failed.connect(seen.append)
    try:
        wait_until(lambda: bool(seen), timeout=15, what="failed signal")
        assert "WebGL" in seen[0] and not v.ready
    finally:
        v.shutdown()


def test_window_uses_the_web_orb_and_can_force_the_native_one(qapp, settings, monkeypatch):
    monkeypatch.delenv("ULTRON_ORB", raising=False)
    win = MainWindow(settings, animate=True)
    try:
        assert isinstance(win.hologram, WebOrbView)
    finally:
        win.close()
    monkeypatch.setenv("ULTRON_ORB", "native")
    win2 = MainWindow(settings, animate=True)
    try:
        assert isinstance(win2.hologram, HologramView)
    finally:
        win2.close()


def test_window_swaps_to_the_native_orb_when_the_web_one_fails(qapp, settings, monkeypatch):
    monkeypatch.delenv("ULTRON_ORB", raising=False)
    win = MainWindow(settings, animate=True)
    try:
        win.show()
        win._orb_failed("no WebGL")
        assert isinstance(win.hologram, HologramView) and win.hologram.isVisible()
        assert "fell back" in win.notice.text() and "no WebGL" in win.notice.text()
    finally:
        win.close()


def test_orb_dir_points_at_the_built_export():
    assert (ORB_DIR / "index.html").is_file()


def test_a_hidden_orb_is_frozen_and_wakes_when_shown_again(qapp, orb):
    from PySide6.QtWebEngineCore import QWebEnginePage

    state = QWebEnginePage.LifecycleState
    assert orb.page.lifecycleState() == state.Active
    orb.hide()
    wait_until(lambda: orb.page.lifecycleState() == state.Frozen, what="page frozen")
    orb.show()
    wait_until(lambda: orb.page.lifecycleState() == state.Active, what="page active again")
